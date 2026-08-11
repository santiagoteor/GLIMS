import pandas as pd

from code.common.constants import (
    BIKE_PREPARATION_TIME_PER_ROUTE_MIN,
    WALKING_PREPARATION_TIME_PER_ROUTE_MIN,
)
from code.common.cost_utils import (
    CostBreakdown,
    calculate_additional_costs,
    calculate_direct_route_operating_cost,
    get_cost_parameter,
)
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
    total_km: float,
    trip_count: int,
    co2_kg: float,
    costs: CostBreakdown,
):
    """Build one result row with a common, auditable cost breakdown."""

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
        "km_recorridos": total_km,
        "numero_viajes": trip_count,
        "emisiones_co2_kg": co2_kg,
        "costo_operacion_ruta_eur": costs.route_operating_cost,
        "costo_servicio_facility_eur": costs.facility_service_cost,
        "otros_costos_eur": (
            costs.facility_fixed_cost
            + costs.warehouse_fixed_cost
            + costs.warehouse_handling_cost
            + costs.vehicle_fixed_cost
            + costs.capital_allocation_cost
            + costs.customer_time_cost
            + costs.carbon_cost
        ),
        "costo_total_eur": costs.total_cost,
        "costo_por_paquete_eur": (
            costs.total_cost / package_count
            if package_count > 0
            else 0.0
        ),
    }


def _build_cost_breakdown(
    *,
    route_distance_cost: float,
    route_labor_cost: float,
    additional: dict[str, float],
) -> CostBreakdown:
    return CostBreakdown(
        route_distance_cost=float(route_distance_cost),
        route_labor_cost=float(route_labor_cost),
        **additional,
    )


def simulate_m1(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    route_plan: OsrmRoutePlan,
    parameters: dict,
    cost_parameters: dict[str, float],
):
    """Simulate conventional-van home delivery from the logistics center."""

    model = parameters["FURGONETA_CONV"]
    distance_cost, labor_cost, _ = calculate_direct_route_operating_cost(
        distance_km=route_plan.total_distance_km,
        total_duration_min=route_plan.total_duration_min,
        route_count=route_plan.route_count,
        route_start_time_per_route_min=route_plan.route_start_time_per_route_min,
        cost_per_km=get_cost_parameter(
            cost_parameters, "conventional_van_cost_per_km"
        ),
        labor_cost_per_hour=get_cost_parameter(
            cost_parameters, "conventional_van_labor_cost_per_hour"
        ),
    )
    co2_kg = (route_plan.total_distance_km * model["co2_km"]) / 1000

    additional = calculate_additional_costs(
        package_count=package_count,
        route_count=route_plan.route_count,
        used_facility_count=0,
        co2_kg=co2_kg,
        customer_travel_min=0.0,
        warehouse_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "warehouse_fixed_cost_per_day"
        ),
        warehouse_handling_cost_per_package=get_cost_parameter(
            cost_parameters, "warehouse_handling_cost_per_package"
        ),
        vehicle_fixed_cost_per_route=get_cost_parameter(
            cost_parameters, "conventional_van_fixed_cost_per_route"
        ),
        vehicle_capex_allocation_per_route=get_cost_parameter(
            cost_parameters, "conventional_van_capex_allocation_per_route"
        ),
        carbon_cost_per_kg=get_cost_parameter(
            cost_parameters, "carbon_cost_per_kg"
        ),
    )
    costs = _build_cost_breakdown(
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        additional=additional,
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M1: Furgoneta Combustión desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        total_km=route_plan.total_distance_km,
        trip_count=route_plan.route_count,
        co2_kg=co2_kg,
        costs=costs,
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
    cost_parameters: dict[str, float],
):
    """Simulate electric-van home delivery from the logistics center."""

    model = parameters["FURGONETA_ELEC"]
    distance_cost, labor_cost, _ = calculate_direct_route_operating_cost(
        distance_km=route_plan.total_distance_km,
        total_duration_min=route_plan.total_duration_min,
        route_count=route_plan.route_count,
        route_start_time_per_route_min=route_plan.route_start_time_per_route_min,
        cost_per_km=get_cost_parameter(
            cost_parameters, "electric_van_cost_per_km"
        ),
        labor_cost_per_hour=get_cost_parameter(
            cost_parameters, "electric_van_labor_cost_per_hour"
        ),
    )
    co2_kg = 0.0

    additional = calculate_additional_costs(
        package_count=package_count,
        route_count=route_plan.route_count,
        used_facility_count=0,
        co2_kg=co2_kg,
        customer_travel_min=0.0,
        warehouse_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "warehouse_fixed_cost_per_day"
        ),
        warehouse_handling_cost_per_package=get_cost_parameter(
            cost_parameters, "warehouse_handling_cost_per_package"
        ),
        vehicle_fixed_cost_per_route=get_cost_parameter(
            cost_parameters, "electric_van_fixed_cost_per_route"
        ),
        vehicle_capex_allocation_per_route=get_cost_parameter(
            cost_parameters, "electric_van_capex_allocation_per_route"
        ),
        carbon_cost_per_kg=get_cost_parameter(
            cost_parameters, "carbon_cost_per_kg"
        ),
    )
    costs = _build_cost_breakdown(
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        additional=additional,
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M2: Furgoneta Eléctrica desde CC",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        total_km=route_plan.total_distance_km,
        trip_count=route_plan.route_count,
        co2_kg=co2_kg,
        costs=costs,
    )


def simulate_m3(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    used_microhub_count: int,
    supply_plan: OsrmRoutePlan,
    bike_distance_km: float,
    bike_duration_min: float,
    bike_route_count: int,
    parameters: dict,
    cost_parameters: dict[str, float],
):
    """Simulate warehouse supply plus multi-microhub cargo-bike delivery."""

    van = parameters["FURGONETA_ELEC"]
    bike = parameters["BICICLETA_CARGO"]

    supply_distance_cost, supply_labor_cost, _ = (
        calculate_direct_route_operating_cost(
            distance_km=supply_plan.total_distance_km,
            total_duration_min=supply_plan.total_duration_min,
            route_count=supply_plan.route_count,
            route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
            cost_per_km=get_cost_parameter(
                cost_parameters, "electric_van_cost_per_km"
            ),
            labor_cost_per_hour=get_cost_parameter(
                cost_parameters, "electric_van_labor_cost_per_hour"
            ),
        )
    )
    bike_distance_cost, bike_labor_cost, _ = (
        calculate_direct_route_operating_cost(
            distance_km=bike_distance_km,
            total_duration_min=bike_duration_min,
            route_count=bike_route_count,
            route_start_time_per_route_min=BIKE_PREPARATION_TIME_PER_ROUTE_MIN,
            cost_per_km=get_cost_parameter(
                cost_parameters, "cargo_bike_cost_per_km"
            ),
            labor_cost_per_hour=get_cost_parameter(
                cost_parameters, "cargo_bike_labor_cost_per_hour"
            ),
        )
    )

    supply_co2 = 0.0  # Electric van: zero direct/tailpipe CO2 emissions.
    route_distance_cost = supply_distance_cost + bike_distance_cost
    route_labor_cost = supply_labor_cost + bike_labor_cost

    # Microhub service cost is configured in cost_parameters.csv.
    microhub_commission = get_cost_parameter(
        cost_parameters, "microhub_commission_per_package"
    )
    additional = calculate_additional_costs(
        package_count=package_count,
        route_count=supply_plan.route_count + bike_route_count,
        used_facility_count=used_microhub_count,
        co2_kg=supply_co2,
        customer_travel_min=0.0,
        facility_commission_per_package=microhub_commission,
        facility_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "microhub_fixed_cost_per_day"
        ),
        warehouse_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "warehouse_fixed_cost_per_day"
        ),
        warehouse_handling_cost_per_package=get_cost_parameter(
            cost_parameters, "warehouse_handling_cost_per_package"
        ),
        vehicle_fixed_cost_per_route=(
            supply_plan.route_count
            * get_cost_parameter(
                cost_parameters, "electric_van_fixed_cost_per_route"
            )
            + bike_route_count
            * get_cost_parameter(
                cost_parameters, "cargo_bike_fixed_cost_per_route"
            )
        ) / max(1, supply_plan.route_count + bike_route_count),
        facility_capex_allocation_per_day=get_cost_parameter(
            cost_parameters, "microhub_capex_allocation_per_day"
        ),
        vehicle_capex_allocation_per_route=(
            supply_plan.route_count
            * get_cost_parameter(
                cost_parameters, "electric_van_capex_allocation_per_route"
            )
            + bike_route_count
            * get_cost_parameter(
                cost_parameters, "cargo_bike_capex_allocation_per_route"
            )
        ) / max(1, supply_plan.route_count + bike_route_count),
        carbon_cost_per_kg=get_cost_parameter(
            cost_parameters, "carbon_cost_per_kg"
        ),
    )
    costs = _build_cost_breakdown(
        route_distance_cost=route_distance_cost,
        route_labor_cost=route_labor_cost,
        additional=additional,
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M3: CC -> Microhubs -> Bicicleta",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        total_km=supply_plan.total_distance_km + bike_distance_km,
        trip_count=supply_plan.route_count + bike_route_count,
        co2_kg=supply_co2,
        costs=costs,
    )


def simulate_m4(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    used_pudo_count: int,
    supply_plan: OsrmRoutePlan,
    walking_distance_km: float,
    walking_duration_min: float,
    walking_route_count: int,
    parameters: dict,
    cost_parameters: dict[str, float],
):
    """Simulate multi-PUDO supply plus courier delivery on foot."""

    van = parameters["FURGONETA_ELEC"]
    walking = parameters["PUDO_A_PIE"]

    supply_distance_cost, supply_labor_cost, _ = (
        calculate_direct_route_operating_cost(
            distance_km=supply_plan.total_distance_km,
            total_duration_min=supply_plan.total_duration_min,
            route_count=supply_plan.route_count,
            route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
            cost_per_km=get_cost_parameter(
                cost_parameters, "electric_van_cost_per_km"
            ),
            labor_cost_per_hour=get_cost_parameter(
                cost_parameters, "electric_van_labor_cost_per_hour"
            ),
        )
    )
    walking_distance_cost, walking_labor_cost, _ = (
        calculate_direct_route_operating_cost(
            distance_km=walking_distance_km,
            total_duration_min=walking_duration_min,
            route_count=walking_route_count,
            route_start_time_per_route_min=WALKING_PREPARATION_TIME_PER_ROUTE_MIN,
            cost_per_km=get_cost_parameter(
                cost_parameters, "walking_cost_per_km"
            ),
            labor_cost_per_hour=get_cost_parameter(
                cost_parameters, "walking_labor_cost_per_hour"
            ),
        )
    )

    supply_co2 = 0.0  # Electric van: zero direct/tailpipe CO2 emissions.
    route_distance_cost = supply_distance_cost + walking_distance_cost
    route_labor_cost = supply_labor_cost + walking_labor_cost
    pudo_commission = get_cost_parameter(
        cost_parameters, "pudo_commission_per_package"
    )

    total_routes = supply_plan.route_count + walking_route_count
    weighted_vehicle_fixed = (
        supply_plan.route_count
        * get_cost_parameter(
            cost_parameters, "electric_van_fixed_cost_per_route"
        )
        + walking_route_count
        * get_cost_parameter(
            cost_parameters, "walking_courier_fixed_cost_per_route"
        )
    ) / max(1, total_routes)
    weighted_vehicle_capex = (
        supply_plan.route_count
        * get_cost_parameter(
            cost_parameters, "electric_van_capex_allocation_per_route"
        )
        + walking_route_count
        * get_cost_parameter(
            cost_parameters, "walking_courier_capex_allocation_per_route"
        )
    ) / max(1, total_routes)

    additional = calculate_additional_costs(
        package_count=package_count,
        route_count=total_routes,
        used_facility_count=used_pudo_count,
        co2_kg=supply_co2,
        customer_travel_min=0.0,
        facility_commission_per_package=pudo_commission,
        facility_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "pudo_fixed_cost_per_day"
        ),
        warehouse_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "warehouse_fixed_cost_per_day"
        ),
        warehouse_handling_cost_per_package=get_cost_parameter(
            cost_parameters, "warehouse_handling_cost_per_package"
        ),
        vehicle_fixed_cost_per_route=weighted_vehicle_fixed,
        facility_capex_allocation_per_day=get_cost_parameter(
            cost_parameters, "pudo_capex_allocation_per_day"
        ),
        vehicle_capex_allocation_per_route=weighted_vehicle_capex,
        carbon_cost_per_kg=get_cost_parameter(
            cost_parameters, "carbon_cost_per_kg"
        ),
    )
    costs = _build_cost_breakdown(
        route_distance_cost=route_distance_cost,
        route_labor_cost=route_labor_cost,
        additional=additional,
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M4: CC -> PUDOs -> Entrega a pie",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        total_km=supply_plan.total_distance_km + walking_distance_km,
        trip_count=total_routes,
        co2_kg=supply_co2,
        costs=costs,
    )


def simulate_m5(
    *,
    city: str,
    neighborhood_name: str,
    selected_cc: pd.Series,
    last_mile_point: OperationalPoint,
    package_count: int,
    customer_count: int,
    used_pudo_count: int,
    supply_plan: OsrmRoutePlan,
    customer_travel_km: float,
    customer_travel_min: float,
    parameters: dict,
    cost_parameters: dict[str, float],
):
    """Simulate multi-PUDO supply plus customer collection travel."""

    van = parameters["FURGONETA_ELEC"]
    model = parameters["PUDO_CONSUMIDOR"]

    distance_cost, labor_cost, _ = calculate_direct_route_operating_cost(
        distance_km=supply_plan.total_distance_km,
        total_duration_min=supply_plan.total_duration_min,
        route_count=supply_plan.route_count,
        route_start_time_per_route_min=supply_plan.route_start_time_per_route_min,
        cost_per_km=get_cost_parameter(
            cost_parameters, "electric_van_cost_per_km"
        ),
        labor_cost_per_hour=get_cost_parameter(
            cost_parameters, "electric_van_labor_cost_per_hour"
        ),
    )
    supply_co2 = 0.0  # Electric van: zero direct/tailpipe CO2 emissions.
    customer_co2 = (
        customer_travel_km * float(model.get("co2_km_estimado_cliente", 0.0) or 0.0)
    ) / 1000
    co2_kg = supply_co2 + customer_co2
    pudo_commission = get_cost_parameter(
        cost_parameters, "pudo_commission_per_package"
    )

    additional = calculate_additional_costs(
        package_count=package_count,
        route_count=supply_plan.route_count,
        used_facility_count=used_pudo_count,
        co2_kg=co2_kg,
        customer_travel_min=customer_travel_min,
        facility_commission_per_package=pudo_commission,
        facility_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "pudo_fixed_cost_per_day"
        ),
        warehouse_fixed_cost_per_day=get_cost_parameter(
            cost_parameters, "warehouse_fixed_cost_per_day"
        ),
        warehouse_handling_cost_per_package=get_cost_parameter(
            cost_parameters, "warehouse_handling_cost_per_package"
        ),
        vehicle_fixed_cost_per_route=get_cost_parameter(
            cost_parameters, "electric_van_fixed_cost_per_route"
        ),
        facility_capex_allocation_per_day=get_cost_parameter(
            cost_parameters, "pudo_capex_allocation_per_day"
        ),
        vehicle_capex_allocation_per_route=get_cost_parameter(
            cost_parameters, "electric_van_capex_allocation_per_route"
        ),
        customer_time_cost_per_hour=get_cost_parameter(
            cost_parameters, "customer_time_cost_per_hour"
        ),
        carbon_cost_per_kg=get_cost_parameter(
            cost_parameters, "carbon_cost_per_kg"
        ),
    )
    costs = _build_cost_breakdown(
        route_distance_cost=distance_cost,
        route_labor_cost=labor_cost,
        additional=additional,
    )

    return _build_result(
        city=city,
        neighborhood_name=neighborhood_name,
        model_name="M5: CC -> PUDOs -> Recogida Cliente",
        selected_cc=selected_cc,
        last_mile_point=last_mile_point,
        package_count=package_count,
        total_km=supply_plan.total_distance_km + customer_travel_km,
        trip_count=supply_plan.route_count + customer_count,
        co2_kg=co2_kg,
        costs=costs,
    )
