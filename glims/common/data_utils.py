import pandas as pd


def validate_required_columns(
    dataframe: pd.DataFrame,
    required_columns: set[str],
    dataset_name: str,
) -> None:
    """Raise an error when a dataframe lacks required columns."""

    missing_columns = required_columns.difference(dataframe.columns)

    if missing_columns:
        raise ValueError(
            f"{dataset_name} is missing required columns: "
            f"{sorted(missing_columns)}"
        )


def get_parameters(
    parameters: pd.DataFrame,
) -> dict[str, dict]:
    """Convert the model parameter table into a model-indexed dictionary."""

    validate_required_columns(
        parameters,
        required_columns={"modelo"},
        dataset_name="model parameters",
    )

    return (
        parameters
        .set_index("modelo")
        .to_dict(orient="index")
    )