from fastapi import APIRouter

api_router = APIRouter(prefix="/api")

# Rutas
from .gps import router as gps_router

# Migrar rutas a api_router
api_router.include_router(gps_router)