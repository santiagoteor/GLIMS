"""Aggregate a microhub M3 scenario and its direct-delivery fallback.

The two delivery legs serve disjoint customers. Fixed warehouse overhead is
charged once. All variable costs, distance, routes and emissions are additive.
"""

ADDITIVE = (
    "km_recorridos", "network_km", "last_meter_access_km",
    "last_meter_access_time_min", "numero_viajes", "direct_km",
    "direct_trip_count", "emisiones_co2_kg", "emisiones_nox_kg",
    "costo_operacion_ruta_eur", "costo_distancia_eur", "costo_laboral_eur",
    "costo_vehiculo_fijo_eur", "costo_manipulacion_eur", "costo_carbono_eur",
)


def combine_m3_fallback(microhub, direct, *, microhub_packages, fallback_packages):
    result = dict(microhub)
    for key in ADDITIVE:
        result[key] = float(microhub.get(key, 0) or 0) + float(direct.get(key, 0) or 0)
    # CAPEX contains vehicle allocations and is additive; fixed warehouse and
    # microhub facility fees stay in M3, never charged again by fallback.
    result["costo_capex_eur"] = float(microhub.get("costo_capex_eur", 0) or 0) + float(direct.get("costo_capex_eur", 0) or 0)
    result["paquetes"] = microhub_packages + fallback_packages
    result["m3_microhub_packages"] = microhub_packages
    result["m3_fallback_packages"] = fallback_packages
    result["m3_fallback_enabled"] = True
    result["modelo"] = "M3: Microhubs + reparto directo complementario"
    result["otros_costos_eur"] = sum(float(result.get(k, 0) or 0) for k in (
        "costo_facility_fijo_eur", "costo_almacen_fijo_eur",
        "costo_manipulacion_eur", "costo_vehiculo_fijo_eur",
        "costo_capex_eur", "costo_tiempo_cliente_eur", "costo_carbono_eur"))
    result["costo_total_eur"] = (
        float(microhub.get("costo_total_eur", 0) or 0)
        + float(direct.get("costo_total_eur", 0) or 0)
        - float(direct.get("costo_almacen_fijo_eur", 0) or 0)
    )
    packages = result["paquetes"]
    result["costo_por_paquete_eur"] = result["costo_total_eur"] / packages if packages else 0
    result["co2_kg_por_paquete"] = result["emisiones_co2_kg"] / packages if packages else 0
    result["nox_g_por_paquete"] = 1000 * result["emisiones_nox_kg"] / packages if packages else 0
    km = result["network_km"]
    result["co2_kg_por_km"] = result["emisiones_co2_kg"] / km if km else 0
    return result
