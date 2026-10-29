"""Gurobi IE MCP server.

Gives Claude industrial-engineering optimisation tools backed by the Gurobi solver:
production planning, facility location, machine scheduling, a general LP/MIP
builder for live "what-if" modelling, and Excel input/output.

Runs with the free size-limited licence that ships with `pip install gurobipy`
(up to 2000 variables and 2000 constraints), so no licence setup is needed for
course-sized models.
"""

from __future__ import annotations

import os
from typing import Any

import gurobipy as gp
from gurobipy import GRB
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gurobi-ie")

STATUS = {
    GRB.OPTIMAL: "optimal",
    GRB.INFEASIBLE: "infeasible",
    GRB.UNBOUNDED: "unbounded",
    GRB.INF_OR_UNBD: "infeasible_or_unbounded",
    GRB.TIME_LIMIT: "time_limit",
}


def _env() -> gp.Env:
    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 0)
    env.start()
    return env


def _status(m: gp.Model) -> str:
    return STATUS.get(m.Status, f"status_{m.Status}")


def _iis(m: gp.Model) -> list[str]:
    """Names of the smallest set of constraints that conflict (Irreducible Inconsistent Subsystem)."""
    try:
        m.computeIIS()
    except gp.GurobiError:
        return []
    out = [c.ConstrName for c in m.getConstrs() if c.IISConstr]
    out += [f"bound:{v.VarName}" for v in m.getVars() if v.IISLB or v.IISUB]
    return out


def _r(x: float, nd: int = 3) -> float:
    return round(float(x), nd)


# --------------------------------------------------------------------------- #
# 1. Aggregate production planning
# --------------------------------------------------------------------------- #
@mcp.tool()
def production_plan(
    products: list[str],
    demand_units: dict[str, list[float]],
    capacity_hours_per_period: list[float],
    hours_per_unit: dict[str, float],
    production_cost_per_unit: dict[str, float],
    holding_cost_per_unit_per_period: dict[str, float],
    initial_inventory_units: dict[str, float] | None = None,
    overtime_hours_max_per_period: float = 0.0,
    overtime_cost_per_hour: float = 0.0,
    backorder_cost_per_unit_per_period: dict[str, float] | None = None,
    setup_cost_per_batch: dict[str, float] | None = None,
    period_labels: list[str] | None = None,
    integer_units: bool = False,
    excel_out_path: str | None = None,
) -> dict[str, Any]:
    """Multi-product, multi-period production plan that minimises total cost.

    Decides how many units of each product to make in each period so that demand is
    met at minimum cost = production + inventory holding + overtime + backorders (+ setups).

    Inputs (all quantities per period, periods are e.g. weeks):
    - products: product names, e.g. ["A", "B"].
    - demand_units: {product: [demand in period 1, period 2, ...]}; every list has the same length T.
    - capacity_hours_per_period: regular machine/labour hours available in each period (length T).
    - hours_per_unit: hours of capacity one unit of each product consumes.
    - production_cost_per_unit, holding_cost_per_unit_per_period: costs in currency units.
    - initial_inventory_units: starting stock per product (default 0).
    - overtime_hours_max_per_period / overtime_cost_per_hour: optional extra hours (default none).
    - backorder_cost_per_unit_per_period: if given, unmet demand may be delayed at this penalty;
      if omitted, demand must be met on time (the model may become infeasible -> see 'conflict').
    - setup_cost_per_batch: if given, a fixed cost is paid in every period a product is produced (MIP).
    - integer_units: True to produce whole units only (MIP; shadow prices are then not reported).
    - excel_out_path: optional .xlsx path; writes the plan, inventory and a capacity chart.

    Returns the plan per period, inventory, overtime, capacity utilisation, cost breakdown,
    and the shadow price of capacity (how much total cost would drop per extra hour in each
    period; only for LP models without setups). If infeasible, returns the conflicting constraints.
    """
    T = len(capacity_hours_per_period)
    labels = period_labels or [f"P{t + 1}" for t in range(T)]
    for p in products:
        if len(demand_units[p]) != T:
            raise ValueError(f"demand_units['{p}'] has {len(demand_units[p])} periods, expected {T}")
    inv0 = initial_inventory_units or {}
    bo_cost = backorder_cost_per_unit_per_period
    setup = setup_cost_per_batch

    m = gp.Model("production_plan", env=_env())
    x = m.addVars(products, range(T), vtype=GRB.INTEGER if integer_units else GRB.CONTINUOUS, name="make")
    s = m.addVars(products, range(T), name="inv")
    ot = m.addVars(range(T), ub=overtime_hours_max_per_period, name="overtime")
    b = m.addVars(products, range(T), name="backorder") if bo_cost else None
    y = m.addVars(products, range(T), vtype=GRB.BINARY, name="setup") if setup else None

    for p in products:
        for t in range(T):
            prev_inv = inv0.get(p, 0.0) if t == 0 else s[p, t - 1]
            prev_bo = 0 if (not b or t == 0) else b[p, t - 1]
            cur_bo = b[p, t] if b else 0
            m.addConstr(
                prev_inv - prev_bo + x[p, t] - demand_units[p][t] == s[p, t] - cur_bo,
                name=f"balance[{p},{labels[t]}]",
            )
            if y is not None:
                big_m = sum(demand_units[p]) + 1
                m.addConstr(x[p, t] <= big_m * y[p, t], name=f"setup_link[{p},{labels[t]}]")
        if b:
            m.addConstr(b[p, T - 1] == 0, name=f"no_backorder_at_end[{p}]")

    cap = {}
    for t in range(T):
        cap[t] = m.addConstr(
            gp.quicksum(hours_per_unit[p] * x[p, t] for p in products) <= capacity_hours_per_period[t] + ot[t],
            name=f"capacity[{labels[t]}]",
        )

    cost_prod = gp.quicksum(production_cost_per_unit[p] * x[p, t] for p in products for t in range(T))
    cost_hold = gp.quicksum(holding_cost_per_unit_per_period[p] * s[p, t] for p in products for t in range(T))
    cost_ot = gp.quicksum(overtime_cost_per_hour * ot[t] for t in range(T))
    cost_bo = gp.quicksum(bo_cost[p] * b[p, t] for p in products for t in range(T)) if b else 0
    cost_setup = gp.quicksum(setup[p] * y[p, t] for p in products for t in range(T)) if y is not None else 0
    m.setObjective(cost_prod + cost_hold + cost_ot + cost_bo + cost_setup, GRB.MINIMIZE)
    m.optimize()

    st = _status(m)
    if m.Status != GRB.OPTIMAL:
        return {"status": st, "conflict": _iis(m) if m.Status in (GRB.INFEASIBLE, GRB.INF_OR_UNBD) else []}

    rows = []
    for t in range(T):
        used = sum(hours_per_unit[p] * x[p, t].X for p in products)
        row = {
            "period": labels[t],
            "make": {p: _r(x[p, t].X, 2) for p in products},
            "inventory_end": {p: _r(s[p, t].X, 2) for p in products},
            "overtime_hours": _r(ot[t].X, 2),
            "hours_used": _r(used, 2),
            "utilisation_pct": _r(100 * used / capacity_hours_per_period[t], 1) if capacity_hours_per_period[t] else None,
        }
        if b:
            row["backorder"] = {p: _r(b[p, t].X, 2) for p in products}
        rows.append(row)

    result: dict[str, Any] = {
        "status": st,
        "total_cost": _r(m.ObjVal, 2),
        "cost_breakdown": {
            "production": _r(cost_prod.getValue(), 2),
            "holding": _r(cost_hold.getValue(), 2),
            "overtime": _r(cost_ot.getValue(), 2),
            "backorder": _r(cost_bo.getValue(), 2) if b else 0,
            "setup": _r(cost_setup.getValue(), 2) if y is not None else 0,
        },
        "plan": rows,
    }
    if y is None and not integer_units:
        result["capacity_shadow_price_per_hour"] = {labels[t]: abs(_r(-cap[t].Pi, 3)) for t in range(T)}
        result["note"] = "Shadow price = cost saved by one extra regular hour in that period (0 = capacity not binding)."
    if excel_out_path:
        result["excel_file"] = _write_plan_excel(excel_out_path, products, labels, rows, capacity_hours_per_period, result)
    return result


def _write_plan_excel(path, products, labels, rows, capacity, result) -> str:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Font

    path = os.path.abspath(os.path.expanduser(path))
    wb = Workbook()
    ws = wb.active
    ws.title = "Plan"
    head = ["Period"] + [f"Make {p}" for p in products] + [f"Inv {p}" for p in products] + [
        "Overtime h", "Hours used", "Capacity h", "Utilisation %"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r, cap in zip(rows, capacity):
        ws.append([r["period"]] + [r["make"][p] for p in products] + [r["inventory_end"][p] for p in products]
                  + [r["overtime_hours"], r["hours_used"], cap, r["utilisation_pct"]])
    n = len(rows) + 1
    ws.append([])
    ws.append(["Total cost", result["total_cost"]])
    for k, v in result["cost_breakdown"].items():
        ws.append([f"  {k}", v])

    hu = len(products) * 2 + 3  # column index of "Hours used"
    bar = BarChart()
    bar.title = "Capacity: hours used vs available"
    bar.y_axis.title = "hours"
    bar.add_data(Reference(ws, min_col=hu, min_row=1, max_row=n), titles_from_data=True)
    bar.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n))
    line = LineChart()
    line.add_data(Reference(ws, min_col=hu + 1, min_row=1, max_row=n), titles_from_data=True)
    bar += line
    bar.width, bar.height = 18, 9
    ws.add_chart(bar, f"A{n + 10}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb.save(path)
    return path


# --------------------------------------------------------------------------- #
# 2. Capacitated facility location
# --------------------------------------------------------------------------- #
@mcp.tool()
def facility_location(
    facilities: list[str],
    customers: list[str],
    fixed_cost_per_facility: dict[str, float],
    capacity_units_per_facility: dict[str, float],
    demand_units_per_customer: dict[str, float],
    shipping_cost_per_unit: dict[str, dict[str, float]],
    max_open_facilities: int | None = None,
) -> dict[str, Any]:
    """Choose which warehouses/plants to open and how to serve customers at minimum cost (MIP).

    - facilities / customers: names.
    - fixed_cost_per_facility: one-off cost of opening each facility (currency).
    - capacity_units_per_facility: max units a facility can ship.
    - demand_units_per_customer: units each customer needs (must be fully met).
    - shipping_cost_per_unit: {facility: {customer: cost per unit shipped}}.
    - max_open_facilities: optional limit on how many may be opened.

    Returns open facilities, shipments, cost split (fixed vs transport) and each open
    facility's utilisation. If infeasible (e.g. total capacity < demand) returns the conflict.
    """
    m = gp.Model("facility_location", env=_env())
    y = m.addVars(facilities, vtype=GRB.BINARY, name="open")
    x = m.addVars(facilities, customers, name="ship")
    for c in customers:
        m.addConstr(x.sum("*", c) == demand_units_per_customer[c], name=f"demand[{c}]")
    for f in facilities:
        m.addConstr(x.sum(f, "*") <= capacity_units_per_facility[f] * y[f], name=f"capacity[{f}]")
    if max_open_facilities is not None:
        m.addConstr(y.sum() <= max_open_facilities, name="max_open")
    fixed = gp.quicksum(fixed_cost_per_facility[f] * y[f] for f in facilities)
    ship = gp.quicksum(shipping_cost_per_unit[f][c] * x[f, c] for f in facilities for c in customers)
    m.setObjective(fixed + ship, GRB.MINIMIZE)
    m.optimize()
    if m.Status != GRB.OPTIMAL:
        return {"status": _status(m), "conflict": _iis(m)}
    opened = [f for f in facilities if y[f].X > 0.5]
    return {
        "status": "optimal",
        "total_cost": _r(m.ObjVal, 2),
        "fixed_cost": _r(fixed.getValue(), 2),
        "transport_cost": _r(ship.getValue(), 2),
        "open_facilities": opened,
        "utilisation_pct": {f: _r(100 * x.sum(f, "*").getValue() / capacity_units_per_facility[f], 1) for f in opened},
        "shipments": [
            {"from": f, "to": c, "units": _r(x[f, c].X, 2)}
            for f in facilities for c in customers if x[f, c].X > 1e-6
        ],
    }


# --------------------------------------------------------------------------- #
# 3. Parallel-machine scheduling
# --------------------------------------------------------------------------- #
@mcp.tool()
def schedule_jobs(
    jobs: list[str],
    processing_time_hours: dict[str, float],
    machines: list[str],
    due_date_hours: dict[str, float] | None = None,
    weight: dict[str, float] | None = None,
    objective: str = "makespan",
    eligible_machines: dict[str, list[str]] | None = None,
    time_limit_s: float = 20.0,
) -> dict[str, Any]:
    """Assign and sequence jobs on identical parallel machines (MIP).

    - jobs, machines: names. processing_time_hours: time each job takes on any machine.
    - objective: "makespan" (finish all jobs as early as possible) or
      "weighted_tardiness" (minimise sum of weight x hours late; needs due_date_hours).
    - due_date_hours: hours from now when each job is due. weight: priority (default 1).
    - eligible_machines: optional {job: [machines it may run on]}.
    - time_limit_s: solver time limit; result says if it stopped before proving optimality.

    Returns a schedule per machine (start/end hours, in order), makespan, tardiness per job,
    and a text Gantt chart.
    """
    if objective not in ("makespan", "weighted_tardiness"):
        raise ValueError("objective must be 'makespan' or 'weighted_tardiness'")
    if objective == "weighted_tardiness" and not due_date_hours:
        raise ValueError("weighted_tardiness needs due_date_hours")
    w = weight or {}
    p = processing_time_hours
    H = sum(p.values())
    m = gp.Model("schedule", env=_env())
    m.Params.TimeLimit = time_limit_s
    a = m.addVars(jobs, machines, vtype=GRB.BINARY, name="assign")
    start = m.addVars(jobs, name="start")
    end = m.addVars(jobs, name="end")
    cmax = m.addVar(name="makespan")
    late = m.addVars(jobs, name="tardiness")
    for j in jobs:
        allowed = (eligible_machines or {}).get(j, machines)
        m.addConstr(gp.quicksum(a[j, k] for k in allowed) == 1, name=f"assign[{j}]")
        for k in machines:
            if k not in allowed:
                m.addConstr(a[j, k] == 0, name=f"not_eligible[{j},{k}]")
        m.addConstr(end[j] == start[j] + p[j], name=f"duration[{j}]")
        m.addConstr(cmax >= end[j], name=f"makespan[{j}]")
        if due_date_hours:
            m.addConstr(late[j] >= end[j] - due_date_hours[j], name=f"tardy[{j}]")
    order = m.addVars([(i, j) for i in jobs for j in jobs if i < j], vtype=GRB.BINARY, name="before")
    for i in jobs:
        for j in jobs:
            if i < j:
                for k in machines:
                    both = a[i, k] + a[j, k] - 1
                    m.addConstr(end[i] <= start[j] + H * (1 - order[i, j]) + H * (1 - both), name=f"seq1[{i},{j},{k}]")
                    m.addConstr(end[j] <= start[i] + H * order[i, j] + H * (1 - both), name=f"seq2[{i},{j},{k}]")
    if objective == "makespan":
        m.setObjective(cmax, GRB.MINIMIZE)
    else:
        m.setObjective(gp.quicksum(w.get(j, 1.0) * late[j] for j in jobs) + 1e-3 * cmax, GRB.MINIMIZE)
    m.optimize()
    if m.SolCount == 0:
        return {"status": _status(m), "conflict": _iis(m) if m.Status == GRB.INFEASIBLE else []}

    sched = {k: [] for k in machines}
    for j in jobs:
        k = next(k for k in machines if a[j, k].X > 0.5)
        sched[k].append({"job": j, "start_h": _r(start[j].X, 2), "end_h": _r(end[j].X, 2)})
    for k in machines:
        sched[k].sort(key=lambda r: r["start_h"])
    span = max(end[j].X for j in jobs)
    scale = 40 / span if span else 1
    sym = {j: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"[i % 62] for i, j in enumerate(jobs)}
    gantt = []
    for k in machines:
        line = ["."] * 40
        for r in sched[k]:
            a0 = int(round(r["start_h"] * scale))
            a1 = max(int(round(r["end_h"] * scale)), a0 + 1)
            for i in range(a0, min(a1, 40)):
                line[i] = sym[r["job"]]
        gantt.append(f"{k:>10} |{''.join(line)}| ")
    gantt.append("legend: " + ", ".join(f"{sym[j]}={j}" for j in jobs) + f" | width = {_r(span, 2)} h")
    out = {
        "status": _status(m) + ("" if m.Status == GRB.OPTIMAL else " (best found, not proven optimal)"),
        "objective": objective,
        "makespan_h": _r(span, 2),
        "schedule": sched,
        "gantt_text": "\n".join(gantt),
    }
    if due_date_hours:
        out["tardiness_h"] = {j: _r(late[j].X, 2) for j in jobs}
        out["weighted_tardiness"] = _r(sum(w.get(j, 1.0) * late[j].X for j in jobs), 2)
    return out


# --------------------------------------------------------------------------- #
# 4. General LP / MIP builder (for anything the other tools do not cover)
# --------------------------------------------------------------------------- #
@mcp.tool()
def solve_custom_model(
    variables: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    objective: dict[str, float],
    sense: str = "min",
    time_limit_s: float = 30.0,
) -> dict[str, Any]:
    """Build and solve any linear or mixed-integer model written out explicitly.

    Use this when the request does not fit production_plan, facility_location or schedule_jobs
    (e.g. product mix, diet, blending, assignment, staffing, knapsack, transportation).

    - variables: [{"name": "x1", "lb": 0, "ub": null, "type": "continuous"|"integer"|"binary"}]
    - constraints: [{"name": "labour", "coeffs": {"x1": 2, "x2": 3}, "sense": "<="|">="|"==", "rhs": 100}]
    - objective: {"x1": 40, "x2": 30}  (linear coefficients)
    - sense: "min" or "max".

    Returns variable values, objective value, and for pure LPs the shadow price of each
    constraint and the slack (unused amount); for infeasible models, the conflicting constraints.
    """
    m = gp.Model("custom", env=_env())
    m.Params.TimeLimit = time_limit_s
    vt = {"continuous": GRB.CONTINUOUS, "integer": GRB.INTEGER, "binary": GRB.BINARY}
    v = {}
    for d in variables:
        ub = d.get("ub")
        v[d["name"]] = m.addVar(lb=d.get("lb", 0.0), ub=GRB.INFINITY if ub is None else ub,
                                vtype=vt[d.get("type", "continuous")], name=d["name"])
    cons = {}
    for c in constraints:
        expr = gp.quicksum(coef * v[n] for n, coef in c["coeffs"].items())
        s = c["sense"]
        if s == "<=":
            cons[c["name"]] = m.addConstr(expr <= c["rhs"], name=c["name"])
        elif s == ">=":
            cons[c["name"]] = m.addConstr(expr >= c["rhs"], name=c["name"])
        elif s in ("==", "="):
            cons[c["name"]] = m.addConstr(expr == c["rhs"], name=c["name"])
        else:
            raise ValueError(f"unknown sense {s!r} in constraint {c['name']}")
    m.setObjective(gp.quicksum(coef * v[n] for n, coef in objective.items()),
                   GRB.MAXIMIZE if sense.lower().startswith("max") else GRB.MINIMIZE)
    m.optimize()
    if m.SolCount == 0:
        return {"status": _status(m), "conflict": _iis(m) if m.Status in (GRB.INFEASIBLE, GRB.INF_OR_UNBD) else []}
    out: dict[str, Any] = {
        "status": _status(m),
        "objective_value": _r(m.ObjVal, 4),
        "values": {n: _r(var.X, 4) for n, var in v.items()},
    }
    if not m.IsMIP:
        out["constraints"] = {n: {"shadow_price": _r(c.Pi, 4), "slack": _r(c.Slack, 4)} for n, c in cons.items()}
        out["reduced_costs"] = {n: _r(var.RC, 4) for n, var in v.items()}
    return out


# --------------------------------------------------------------------------- #
# 5. Excel input
# --------------------------------------------------------------------------- #
@mcp.tool()
def read_excel_table(path: str, sheet: str | None = None, max_rows: int = 200) -> dict[str, Any]:
    """Read a table from an .xlsx file (first row = headers) so it can be fed into a model.

    Returns the sheet names, headers and rows as lists. Use it to load demand, capacities or
    costs that the user keeps in Excel, then pass the numbers to another tool.
    """
    from openpyxl import load_workbook

    path = os.path.abspath(os.path.expanduser(path))
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet] if sheet else wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    rows = [r for r in rows if any(c is not None for c in r)]
    return {"sheets": wb.sheetnames, "sheet": ws.title, "headers": rows[0] if rows else [],
            "rows": rows[1:max_rows + 1], "row_count": max(len(rows) - 1, 0)}


if __name__ == "__main__":
    mcp.run()
