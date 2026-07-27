from unittest.mock import patch

import numpy as np
import pandas as pd

from code.simulation.osrm_simulator import select_logistics_center


def test_select_logistics_center_uses_round_trip_distance():
    centers = pd.DataFrame(
        {
            "Location": ["CC_A", "CC_B"],
            "Latitude": [40.50, 40.60],
            "Longitude": [-3.60, -3.70],
        }
    )

    fake_matrix = np.array(
        [
            [0.0, 10.0, 7.0],
            [12.0, 0.0, 5.0],
            [20.0, 5.0, 0.0],
        ]
    )

    with patch(
        "code.simulation.osrm_simulator.osrm_table",
        return_value=fake_matrix,
    ):
        selected = select_logistics_center(
            centers=centers,
            neighborhood_lat=40.40,
            neighborhood_lon=-3.65,
        )

    assert selected["Location"] == "CC_A"
    assert selected["distancia_troncal_ida_km"] == 12.0
    assert selected["distancia_troncal_regreso_km"] == 10.0
    assert selected["distancia_troncal_total_km"] == 22.0


if __name__ == "__main__":
    test_select_logistics_center_uses_round_trip_distance()
    print("Test completed successfully.")