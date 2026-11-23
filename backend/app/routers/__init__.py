"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, trial

# trial 路由前缀固定（/api/trial），必须先于 /api/{module} 注册。
ROUTERS = [trial, resource]
