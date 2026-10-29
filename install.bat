@echo off
REM Installs the Python packages for the Gurobi IE MCP server (Windows)
cd /d "%~dp0"
py -m pip install -r requirements.txt || python -m pip install -r requirements.txt
echo.
echo Test: running a tiny model...
py -c "import gurobipy as gp; m=gp.Model(); x=m.addVar(); m.setObjective(x,-1); m.addConstr(x<=3); m.optimize(); print('Gurobi OK, x =', x.X)" || python -c "import gurobipy; print('Gurobi OK')"
echo.
echo Server path for claude_desktop_config.json:
echo %~dp0server.py
pause
