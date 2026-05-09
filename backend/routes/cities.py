from fastapi import APIRouter
from fastapi.responses import JSONResponse
from backend.us_cities import US_CITIES, search_cities, calculate_distance

router = APIRouter()

@router.get("/api/cities/search")
def search_cities_endpoint(q: str = "", limit: int = 10):
    """Search cities by name"""
    if not q:
        return []
    
    results = search_cities(q, limit)
    return results

@router.get("/api/cities/all")
def get_all_cities():
    """Get all cities - cached for 24 hours"""
    return JSONResponse(
        content=US_CITIES,
        headers={"Cache-Control": "public, max-age=86400"}
    )

@router.get("/api/cities/distance")
def calculate_distance_endpoint(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float
):
    """Calculate distance between two coordinates"""
    distance = calculate_distance(lat1, lon1, lat2, lon2)
    return {"distance_miles": round(distance, 2)}
