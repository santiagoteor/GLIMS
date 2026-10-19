"""Operational 3-km Haversine eligibility and auditable unserved M3 demand."""
import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6371008.8

def _distances(customers, facilities):
    a = np.radians(customers[['Latitude', 'Longitude']].to_numpy(dtype=float))
    b = np.radians(facilities[['Latitude', 'Longitude']].to_numpy(dtype=float))
    if not len(a) or not len(b):
        return np.empty((len(a), len(b)))
    dlat = b[None, :, 0] - a[:, None, 0]
    dlon = b[None, :, 1] - a[:, None, 1]
    h = np.sin(dlat / 2)**2 + np.cos(a[:, None, 0])*np.cos(b[None, :, 0])*np.sin(dlon / 2)**2
    return 2*EARTH_RADIUS_M*np.arcsin(np.sqrt(np.clip(h, 0, 1)))

def _id(row, position):
    for col in ('customer_id', 'Customer_ID', 'ID', 'id'):
        if col in row.index and pd.notna(row[col]):
            return str(row[col])
    return str(position)

def _rows(customers, facilities, city, neighborhood, radius_m, reason):
    d = _distances(customers, facilities)
    rows = []
    for i, (_, customer) in enumerate(customers.iterrows()):
        nearest = int(np.argmin(d[i])) if len(facilities) else None
        facility = facilities.iloc[nearest] if nearest is not None else None
        distance = float(d[i, nearest]) if nearest is not None else None
        rows.append({'city':city, 'neighborhood':neighborhood, 'customer_id':_id(customer, i),
                     'latitude':float(customer['Latitude']), 'longitude':float(customer['Longitude']),
                     'packages':float(customer['Demand']),
                     'nearest_microhub_id':str(facility.name) if facility is not None else None,
                     'nearest_microhub_name':str(facility.get('Location','')) if facility is not None else None,
                     'distance_to_nearest_microhub_m':distance,
                     'coverage_radius_m':float(radius_m),
                     'excess_distance_m':max(0.0,distance-radius_m) if distance is not None else None,
                     'exclusion_reason':reason})
    return rows

def classify_m3_customers(customers, facilities, *, city, neighborhood, radius_m):
    d = _distances(customers, facilities)
    mask = np.min(d,axis=1) <= radius_m if len(facilities) else np.zeros(len(customers),dtype=bool)
    eligible = customers.loc[mask].copy().reset_index(drop=True)
    rejected = customers.loc[~mask].copy()
    return eligible, _rows(rejected, facilities, city, neighborhood, radius_m, 'outside_microhub_coverage')

def rejected_capacity_rows(customers, facilities, city, neighborhood, radius_m):
    return _rows(customers, facilities, city, neighborhood, radius_m, 'insufficient_microhub_capacity')
