import pandas as pd

from code.common.constants import BIKE_PREPARATION_TIME_PER_ROUTE_MIN, WALKING_PREPARATION_TIME_PER_ROUTE_MIN
from code.common.cost_utils import calculate_direct_route_operating_cost
from code.routing.route_plan import OsrmRoutePlan
from code.simulation.operational_points import OperationalPoint

def _build_result(
    *,
    city: str,
    neighborhood_name: str,
    model_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    outbound_trunk_distance: float,
    return_trunk_distance: float,
    total_km: float,
    trip_count: int,
    co2_kg: float,
    route_distance_cost: float,
    route_labor_cost: float,
    facility_service_cost: float,
    total_cost: float,
):
    """Build a result row using the common output schema."""

    return {
        "ciudad": city,
        "barrio": neighborhood_name,
        "modelo": model_name,
        "centro_logistico": selected_cc["Location"],
        "punto_ultima_milla": last_mile_point.name,
        "latitud_punto_ultima_milla": last_mile_point.latitude,
        "longitud_punto_ultima_milla": last_mile_point.longitude,
        "tipo_punto_ultima_milla": last_mile_point.point_type,
        "estrategia_punto_ultima_milla": last_mile_point.strategy,
        "paquetes": package_count,
        "distancia_troncal_ida_km": outbound_trunk_distance,
        "distancia_troncal_regreso_km": return_trunk_distance,
        "km_recorridos": total_km,
        "numero_viajes": trip_count,
        "emisiones_co2_kg": co2_kg,
        "costo_distancia_ruta_eur": route_distance_cost,
        "costo_laboral_ruta_eur": route_labor_cost,
        "costo_servicio_facility_eur": facility_service_cost,
        "costo_operacion_ruta_eur": route_distance_cost + route_labor_cost,
        "costo_total_eur": total_cost,
    }



def simulate_m1(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    route_plan: OsrmRoutePlan,
    parameters: dict,
):
    """Simulate conventional-van home delivery from the logistics center."""

    model = parameters["FURGONETA_CONV"]
    distance_cost, labor_cost, route_cost = calculate_direct_route_operating_cost(
        distance_km=route_plan.total_distance_km,
        total_duration_min=route_plan.total_duration_min,
        route_count=route_plan.route_count,
        route_start_time_per_route_min=route_plan.route_start_time_per_route_min,
        cost_per_km=model["costo_km"],
        labor_cost_per_hour=model["costo_hora"],
    )
    co2_kg = (route_plan.total_distance_km * model["co2_km"]) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M1: Furgoneta Combustión desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=route_plan.total_distance_km,
        trip_count=route_plan.route_count,
        co2_kg=co2_kg,
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        facility_service_cost=0.0,
        total_cost=route_cost,
    )

def simulate_m2(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    route_plan: OsrmRoutePlan,
    parameters: dict,
):
    """Simulate electric-van home delivery from the logistics center."""

    model = parameters["FURGONETA_ELEC"]
    distance_cost, labor_cost, route_cost = calculate_direct_route_operating_cost(
        distance_km=route_plan.total_distance_km,
        total_duration_min=route_plan.total_duration_min,
        route_count=route_plan.route_count,
        route_start_time_per_route_min=route_plan.route_start_time_per_route_min,
        cost_per_km=model["costo_km"],
        labor_cost_per_hour=model["costo_hora"],
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M2: Furgoneta Eléctrica desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=route_plan.total_distance_km,
        trip_count=route_plan.route_count,
        co2_kg=0.0,
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        facility_service_cost=0.0,
        total_cost=route_cost,
    )

def simulate_m3(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    supply_plan: OsrmRoutePlan,
    bike_distance_km: float,
    bike_duration_min: float,
    bike_route_count: int,
    parameters: dict,
):
    """Simulate warehouse supply plus multi-microhub cargo-bike delivery."""

    van = parameters["FURGONETA_CONV"]
    bike = parameters["BICICLETA_CARGO"]

    supply_distance_cost, supply_labor_cost, supply_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=supply_plan.total_distance_km,
            total_duration_min=supply_plan.total_duration_min,
            route_count=supply_plan.route_count,
            route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
            cost_per_km=van["costo_km"],
            labor_cost_per_hour=van["costo_hora"],
        )
    )
    bike_distance_cost, bike_labor_cost, bike_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=bike_distance_km,
            total_duration_min=bike_duration_min,
            route_count=bike_route_count,
            route_start_time_per_route_min=BIKE_PREPARATION_TIME_PER_ROUTE_MIN,
            cost_per_km=bike["costo_km"],
            labor_cost_per_hour=bike["costo_hora"],
        )
    )
    facility_service_cost = package_count * float(bike["comision_microhub"])
    supply_co2 = (supply_plan.total_distance_km * van["co2_km"]) / 1000

    route_distance_cost = supply_distance_cost + bike_distance_cost
    route_labor_cost = supply_labor_cost + bike_labor_cost
    total_cost = supply_route_cost + bike_route_cost + facility_service_cost

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M3: CC -> Microhubs -> Bicicleta",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=supply_plan.total_distance_km + bike_distance_km,
        trip_count=supply_plan.route_count + bike_route_count,
        co2_kg=supply_co2,
        route_distance_cost=route_distance_cost,
        route_labor_cost=route_labor_cost,
        facility_service_cost=facility_service_cost,
        total_cost=total_cost,
    )

def simulate_m4(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    supply_plan: OsrmRoutePlan,
    walking_distance_km: float,
    walking_duration_min: float,
    walking_route_count: int,
    parameters: dict,
):
    """Simulate multi-PUDO supply plus courier delivery on foot."""

    van = parameters["FURGONETA_CONV"]
    walking = parameters["PUDO_A_PIE"]

    supply_distance_cost, supply_labor_cost, supply_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=supply_plan.total_distance_km,
            total_duration_min=supply_plan.total_duration_min,
            route_count=supply_plan.route_count,
            route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
            cost_per_km=van["costo_km"],
            labor_cost_per_hour=van["costo_hora"],
        )
    )
    walking_distance_cost, walking_labor_cost, walking_route_cost = (
        calculate_direct_route_operating_cost(
            distance_km=walking_distance_km,
            total_duration_min=walking_duration_min,
            route_count=walking_route_count,
            route_start_time_per_route_min=WALKING_PREPARATION_TIME_PER_ROUTE_MIN,
            cost_per_km=walking["costo_km"],
            labor_cost_per_hour=walking["costo_hora"],
        )
    )
    facility_service_cost = package_count * float(walking["comision_pudo"])

    route_distance_cost = supply_distance_cost + walking_distance_cost
    route_labor_cost = supply_labor_cost + walking_labor_cost
    total_cost = supply_route_cost + walking_route_cost + facility_service_cost
    supply_co2 = (supply_plan.total_distance_km * van["co2_km"]) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M4: CC -> PUDOs -> Entrega a pie",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=supply_plan.total_distance_km + walking_distance_km,
        trip_count=supply_plan.route_count + walking_route_count,
        co2_kg=supply_co2,
        route_distance_cost=route_distance_cost,
        route_labor_cost=route_labor_cost,
        facility_service_cost=facility_service_cost,
        total_cost=total_cost,
    )

def simulate_m5(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    customer_count: int,
    supply_plan: OsrmRoutePlan,
    customer_travel_km: float,
    parameters: dict,
):
    """Simulate multi-PUDO supply plus customer collection travel."""

    van = parameters["FURGONETA_CONV"]
    model = parameters["PUDO_CONSUMIDOR"]

    distance_cost, labor_cost, route_cost = calculate_direct_route_operating_cost(
        distance_km=supply_plan.total_distance_km,
        total_duration_min=supply_plan.total_duration_min,
        route_count=supply_plan.route_count,
        route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
        cost_per_km=van["costo_km"],
        labor_cost_per_hour=van["costo_hora"],
    )
    facility_service_cost = package_count * float(model["comision_pudo"])
    supply_co2 = (supply_plan.total_distance_km * van["co2_km"]) / 1000
    customer_co2 = (
        customer_travel_km * model["co2_km_estimado_cliente"]
    ) / 1000

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M5: CC -> PUDOs -> Recogida Cliente",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        outbound_trunk_distance=0.0,
        return_trunk_distance=0.0,
        total_km=supply_plan.total_distance_km + customer_travel_km,
        trip_count=supply_plan.route_count + customer_count,
        co2_kg=supply_co2 + customer_co2,
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        facility_service_cost=facility_service_cost,
        total_cost=route_cost + facility_service_cost,
    )
