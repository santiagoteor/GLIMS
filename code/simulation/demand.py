import pandas as pd

from code.common.data_utils import validate_required_columns
from code.simulation.operational_points import OperationalPoint


def calculate_demand_weighted_centroid(records: pd.DataFrame) -> OperationalPoint:
    """Return the package-demand-weighted center of the delivery instance."""

    validate_required_columns(
        records,
        required_columns={"Latitude", "Longitude", "Demand"},
        dataset_name="demand records",
    )
    total_demand = float(records["Demand"].sum())
    if total_demand <= 0:
        raise ValueError("Total package demand must be greater than zero.")

    return OperationalPoint(
        name="Demand-weighted neighborhood centroid",
        latitude=float((records["Latitude"] * records["Demand"]).sum() / total_demand),
        longitude=float((records["Longitude"] * records["Demand"]).sum() / total_demand),
        point_type="demand_centroid",
        strategy="demand_weighted_centroid",
        is_virtual=True,
    )
