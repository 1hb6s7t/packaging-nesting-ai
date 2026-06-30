from fastapi import APIRouter

from app.api.routes import (
    adapters,
    ai,
    artworks,
    auth,
    batch_artworks,
    batch_layout,
    benchmark,
    benchmarks,
    health,
    nesting,
    notifications,
    operation_logs,
    orders,
    rbac,
    rules,
    sheets,
    solvers,
    solutions,
    tasks,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
api_router.include_router(artworks.router, prefix="/artworks", tags=["artworks"])
api_router.include_router(batch_artworks.router, prefix="/batch-artworks", tags=["batch-artworks"])
api_router.include_router(batch_layout.router, prefix="/batch-layout", tags=["batch-layout"])
api_router.include_router(sheets.router, prefix="/sheets", tags=["sheets"])
api_router.include_router(nesting.router, prefix="/nesting", tags=["nesting"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(solutions.router, prefix="/solutions", tags=["solutions"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(adapters.router, prefix="/adapters", tags=["adapters"])
api_router.include_router(benchmark.router, prefix="/benchmark", tags=["benchmark"])
api_router.include_router(benchmarks.router, prefix="/benchmarks", tags=["benchmarks"])
api_router.include_router(operation_logs.router, prefix="/operation-logs", tags=["operation-logs"])
api_router.include_router(rbac.router, prefix="/rbac", tags=["rbac"])
api_router.include_router(rules.router, prefix="/rules", tags=["rules"])
api_router.include_router(solvers.router, prefix="/solvers", tags=["solvers"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
