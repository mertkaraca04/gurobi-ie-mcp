# Live demo script — Gurobi IE MCP (about 8 minutes)

Story: a heating-products plant (boilers, heat pumps, water heaters) plans the next 8 weeks. Data is in `demo_demand.xlsx`.

## 0. Show the setup (30 s)
Open Claude Desktop → tools menu → show "gurobi-ie" with its 5 tools. Say: "Claude doesn't solve this itself; it calls the Gurobi solver through MCP, and every tool call is visible."

## 1. Baseline plan (2 min)
> Read demo_demand.xlsx (both sheets) and build an 8-week production plan with whole units using gurobi-ie. Write it to plan_output.xlsx. Which weeks are at full capacity?

Point out: Claude read Excel → called `production_plan` → the plan pre-builds water heaters in weeks 5–6 so that no overtime is needed later. Open plan_output.xlsx and show the chart.

## 2. Live modification: maintenance week (1.5 min)
> Line maintenance cuts week 6 capacity to 700 hours. Re-plan and tell me what changed and what it costs.

Expected: the plan pre-builds more units before week 6; total cost rises by about 18,200 TL. Ask Claude what changed week by week.

## 3. Sensitivity: is an extra hour worth it? (1 min)
> Solve the same plan without the whole-unit restriction and show the capacity shadow prices. Would renting 50 extra hours in week 7 at 500 TL/hour pay off?

Expected: week 7 shadow price is 80 TL/hour, far below 500 TL/hour, so renting the extra hours does not pay off.

## 4. Infeasibility explained (1 min)
> A supplier delay limits weeks 1 and 2 to 300 regular hours (overtime still max 120 h). Re-plan.

Expected: status **infeasible**, and the conflict list points only at weeks 1–2 (week 1 alone needs 435 h but only 420 h exist). Then:
> Allow backorders at 300 TL per unit per week and solve again.

Expected: feasible again. Talking point: Claude chooses to delay 85 boilers rather than pay overtime, because a week's delay (300 TL) is cheaper than the 2 overtime hours one boiler needs (2 × 650 = 1300 TL).

## 5. A second problem type (1.5 min, pick one)
**Scheduling:**
> Schedule these jobs on 2 CNC machines to minimise weighted tardiness: J1 3 h due 4, J2 5 h due 8, J3 2 h due 3, J4 4 h due 10, J5 6 h due 12, J6 1 h due 2. Show the Gantt chart.

**Facility location:**
> We may open warehouses in İzmir (fixed 5000, capacity 300), Manisa (3000, 200) and Aydın (3500, 250). Customers C1=120, C2=150, C3=100 units. Shipping cost per unit: İzmir 2/4/5, Manisa 3/1/6, Aydın 6/5/2. Which warehouses should we open?

## 6. Close (30 s)
"MCP turned a solver we normally drive with code into something we can question in plain language, but the model and its assumptions are still our responsibility." Mention: free size-limited licence, 5 tools, about 400 lines of Python.

## Hand-checks to mention (from the handout's Exercise 2)
- Week 1 hours: 90 × 2 + 30 × 3.5 + 150 × 1 = 435 h ≤ 900 h. ✔
- Facility answer: Manisa + Aydın capacity 450 ≥ demand 370; fixed 6500 + transport 920 = 7420. ✔
