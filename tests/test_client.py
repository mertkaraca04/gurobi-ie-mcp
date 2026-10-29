import asyncio, json, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["../server.py"])) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            print([t.name for t in (await s.list_tools()).tools])
            async def call(n,a):
                res=await s.call_tool(n,a); print("==",n, "ERR" if res.isError else ""); import json as J; d=J.loads(res.content[0].text); print(J.dumps(d)[:900])
            pp=dict(products=["A","B"],demand_units={"A":[100,150,200,120],"B":[80,60,120,140]},
                capacity_hours_per_period=[400,400,400,400],hours_per_unit={"A":1.5,"B":2.0},
                production_cost_per_unit={"A":10,"B":14},holding_cost_per_unit_per_period={"A":1,"B":1.5},
                overtime_hours_max_per_period=60,overtime_cost_per_hour=25,excel_out_path="plan_test.xlsx")
            await call("production_plan",pp)
            pp2=dict(pp,capacity_hours_per_period=[300,300,300,300],overtime_hours_max_per_period=0); pp2.pop("excel_out_path")
            await call("production_plan",pp2)
            pp3=dict(pp2,backorder_cost_per_unit_per_period={"A":5,"B":6},setup_cost_per_batch={"A":200,"B":200},capacity_hours_per_period=[450]*4)
            await call("production_plan",pp3)
            await call("facility_location",dict(facilities=["Izmir","Manisa","Aydin"],customers=["C1","C2","C3"],
                fixed_cost_per_facility={"Izmir":5000,"Manisa":3000,"Aydin":3500},capacity_units_per_facility={"Izmir":300,"Manisa":200,"Aydin":250},
                demand_units_per_customer={"C1":120,"C2":150,"C3":100},
                shipping_cost_per_unit={"Izmir":{"C1":2,"C2":4,"C3":5},"Manisa":{"C1":3,"C2":1,"C3":6},"Aydin":{"C1":6,"C2":5,"C3":2}}))
            await call("schedule_jobs",dict(jobs=["J1","J2","J3","J4","J5","J6"],processing_time_hours={"J1":3,"J2":5,"J3":2,"J4":4,"J5":6,"J6":1},
                machines=["M1","M2"],due_date_hours={"J1":4,"J2":8,"J3":3,"J4":10,"J5":12,"J6":2},objective="weighted_tardiness"))
            await call("solve_custom_model",dict(variables=[{"name":"chairs"},{"name":"tables"}],
                constraints=[{"name":"wood","coeffs":{"chairs":2,"tables":5},"sense":"<=","rhs":200},{"name":"labour","coeffs":{"chairs":3,"tables":4},"sense":"<=","rhs":180}],
                objective={"chairs":40,"tables":90},sense="max"))
            await call("read_excel_table",dict(path="plan_test.xlsx"))
asyncio.run(main())
