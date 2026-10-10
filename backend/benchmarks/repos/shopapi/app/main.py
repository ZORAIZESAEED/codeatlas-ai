from fastapi import FastAPI

from app.api import auth_routes, product_routes
from app.db.session import init_engine


def create_app() -> FastAPI:
    """Build the FastAPI application, register routers and open the database on startup."""
    app = FastAPI(title="Shop API")
    app.include_router(auth_routes.router)
    app.include_router(product_routes.router)

    @app.on_event("startup")
    def startup() -> None:
        init_engine()

    return app


app = create_app()
