#!/usr/bin/env python3
"""
GO-MO: construir perfiles históricos de referencia de 15 minutos con bajo uso de RAM.

Estrategia:
1) Procesar traffic_data_2024.csv mes a mes con DuckDB.
2) Guardar un Parquet intermedio por mes.
3) Combinar los 12 meses en un único:
       reference_profiles_15min.parquet

Granularidad final:
    sensor_id × weekday × hour × minute

Reglas de calidad por defecto:
- read_error == 'N'
- traffic_intensity y sensor_occupancy no nulos y >= 0
- se excluyen filas donde intensidad u ocupación fueron interpoladas
- avg_speed NO es necesaria para construir la referencia principal
- las estadísticas de velocidad se conservan solo como diagnóstico

Uso recomendado:
    python code/preprocessing/build_reference_profiles_15min.py \
      --input data/external/go_mo/traffic_data_2024.csv \
      --output data/external/go_mo/processed/reference_profiles_15min.parquet \
      --start-hour 7 \
      --end-hour 20 \
      --memory-limit 2GB \
      --threads 2 \
      --temp-dir data/external/go_mo/duckdb_tmp \
      --overwrite
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import duckdb


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Construye perfiles GO-MO de referencia de 15 minutos "
            "procesando el CSV por meses para reducir el uso de RAM."
        )
    )

    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Ruta a traffic_data_2024.csv",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Ruta de salida para reference_profiles_15min.parquet",
    )
    parser.add_argument(
        "--start-hour",
        type=int,
        default=7,
        help="Primera hora incluida (default: 7)",
    )
    parser.add_argument(
        "--end-hour",
        type=int,
        default=20,
        help="Última hora incluida, inclusive (default: 20)",
    )
    parser.add_argument(
        "--weekdays-only",
        action="store_true",
        help="Conservar solo lunes-viernes. Por defecto usa toda la semana.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=2,
        help="Número de hilos DuckDB (default: 2)",
    )
    parser.add_argument(
        "--memory-limit",
        default="2GB",
        help="Límite de memoria de DuckDB, por ejemplo 2GB, 4GB (default: 2GB)",
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=None,
        help="Directorio para temporales de DuckDB",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directorio para los Parquet mensuales intermedios",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescribir salida/intermedios existentes",
    )
    parser.add_argument(
        "--keep-monthly",
        action="store_true",
        help="Conservar los Parquet mensuales al terminar",
    )

    return parser.parse_args()


def sql_path(path: Path) -> str:
    return (
        str(path.resolve())
        .replace("\\", "/")
        .replace("'", "''")
    )


def make_connection(
    memory_limit: str,
    threads: int,
    temp_dir: Path,
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")

    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET threads={max(1, threads)}")
    con.execute(f"SET temp_directory='{sql_path(temp_dir)}'")

    # Reduce memoria asociada al mantenimiento del orden de inserción.
    con.execute("SET preserve_insertion_order=false")

    # Barra de progreso en consola.
    con.execute("PRAGMA enable_progress_bar")

    return con


def main() -> int:
    args = parse_args()

    input_path = args.input.resolve()
    output_path = args.output.resolve()

    if not input_path.exists():
        print(
            f"ERROR: no existe el archivo de entrada:\n{input_path}",
            file=sys.stderr,
        )
        return 2

    if not (0 <= args.start_hour <= args.end_hour <= 23):
        print(
            "ERROR: --start-hour y --end-hour deben estar entre 0 y 23 "
            "y start <= end.",
            file=sys.stderr,
        )
        return 2

    if output_path.exists() and not args.overwrite:
        print(
            f"ERROR: ya existe:\n{output_path}\n"
            "Usa --overwrite para reemplazarlo.",
            file=sys.stderr,
        )
        return 2

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_dir = (
        args.temp_dir.resolve()
        if args.temp_dir is not None
        else (output_path.parent / "duckdb_tmp").resolve()
    )
    temp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    work_dir = (
        args.work_dir.resolve()
        if args.work_dir is not None
        else (
            output_path.parent
            / "_reference_profiles_15min_monthly"
        ).resolve()
    )
    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_path.exists() and args.overwrite:
        output_path.unlink()

    weekday_filter = (
        "AND dayofweek(entry_date) BETWEEN 1 AND 5"
        if args.weekdays_only
        else ""
    )

    print("=" * 76)
    print("GO-MO 15-minute reference builder — LOW MEMORY / MONTHLY")
    print("=" * 76)
    print(f"Input:          {input_path}")
    print(
        f"Input size:     "
        f"{input_path.stat().st_size / 1024**3:.2f} GB"
    )
    print(f"Output:         {output_path}")
    print(
        f"Hours:          "
        f"{args.start_hour:02d}:00–{args.end_hour:02d}:59"
    )
    print(f"Weekdays only:  {args.weekdays_only}")
    print(f"Memory limit:   {args.memory_limit}")
    print(f"Threads:        {args.threads}")
    print(f"Temp dir:       {temp_dir}")
    print(f"Monthly work:   {work_dir}")
    print()

    started_all = time.perf_counter()
    input_sql = sql_path(input_path)

    # ==========================================================
    # FASE 1: procesar cada mes por separado
    # ==========================================================

    for month in range(1, 13):
        monthly_path = (
            work_dir
            / f"month_{month:02d}.parquet"
        )

        if (
            monthly_path.exists()
            and not args.overwrite
        ):
            print(
                f"[{month:02d}/12] Ya existe; "
                f"reutilizando {monthly_path.name}"
            )
            continue

        if monthly_path.exists():
            monthly_path.unlink()

        print(
            f"\n[{month:02d}/12] "
            f"Procesando mes {month:02d}..."
        )

        started_month = time.perf_counter()
        con = make_connection(
            args.memory_limit,
            args.threads,
            temp_dir,
        )

        monthly_sql = f"""
        COPY (
            WITH source AS (
                SELECT
                    CAST(sensor_id AS VARCHAR) AS sensor_id,
                    CAST(entry_date AS TIMESTAMP) AS entry_date,
                    TRY_CAST(
                        traffic_intensity AS BIGINT
                    ) AS traffic_intensity,
                    TRY_CAST(
                        sensor_occupancy AS BIGINT
                    ) AS sensor_occupancy,
                    TRY_CAST(
                        avg_speed AS DOUBLE
                    ) AS avg_speed,
                    CAST(
                        read_error AS VARCHAR
                    ) AS read_error,
                    LPAD(
                        COALESCE(
                            CAST(
                                is_interpolated AS VARCHAR
                            ),
                            ''
                        ),
                        3,
                        '0'
                    ) AS interp
                FROM read_csv(
                    '{input_sql}',
                    header = true,
                    columns = {{
                        'sensor_id': 'VARCHAR',
                        'entry_date': 'TIMESTAMP',
                        'traffic_intensity': 'BIGINT',
                        'sensor_occupancy': 'BIGINT',
                        'avg_speed': 'DOUBLE',
                        'read_error': 'VARCHAR',
                        'is_interpolated': 'VARCHAR'
                    }},
                    auto_detect = false
                )
                WHERE
                    month(entry_date) = {month}
                    AND hour(entry_date)
                        BETWEEN {args.start_hour}
                        AND {args.end_hour}
                    {weekday_filter}
            ),
            valid AS (
                SELECT *
                FROM source
                WHERE
                    read_error = 'N'
                    AND traffic_intensity IS NOT NULL
                    AND sensor_occupancy IS NOT NULL
                    AND traffic_intensity >= 0
                    AND sensor_occupancy >= 0
                    AND SUBSTR(interp, 1, 1) <> '1'
                    AND SUBSTR(interp, 2, 1) <> '1'
            )
            SELECT
                sensor_id,
                (
                    (dayofweek(entry_date) + 6) % 7
                )::UTINYINT AS weekday,
                hour(entry_date)::UTINYINT AS hour_slot,
                minute(entry_date)::UTINYINT AS minute_slot,
                traffic_intensity,
                sensor_occupancy,

                COUNT(*)::BIGINT AS n_obs,

                SUM(
                    traffic_intensity
                )::DOUBLE AS intensity_sum,

                SUM(
                    sensor_occupancy
                )::DOUBLE AS occupancy_sum,

                COUNT(*) FILTER (
                    WHERE
                        avg_speed >= 5
                        AND SUBSTR(interp, 3, 1) <> '1'
                )::BIGINT AS n_speed_obs,

                SUM(avg_speed) FILTER (
                    WHERE
                        avg_speed >= 5
                        AND SUBSTR(interp, 3, 1) <> '1'
                )::DOUBLE AS speed_sum

            FROM valid

            GROUP BY
                sensor_id,
                weekday,
                hour_slot,
                minute_slot,
                traffic_intensity,
                sensor_occupancy
        )
        TO '{sql_path(monthly_path)}'
        (
            FORMAT PARQUET,
            COMPRESSION ZSTD
        );
        """

        try:
            con.execute(monthly_sql)
        finally:
            con.close()

        elapsed_month = (
            time.perf_counter()
            - started_month
        )

        print(
            f"[{month:02d}/12] OK — "
            f"{elapsed_month / 60:.2f} min — "
            f"{monthly_path.stat().st_size / 1024**2:.1f} MB"
        )

    # ==========================================================
    # FASE 2: combinar los meses
    # ==========================================================

    print()
    print("=" * 76)
    print("Combinando los 12 agregados mensuales...")
    print("=" * 76)

    con = make_connection(
        args.memory_limit,
        args.threads,
        temp_dir,
    )

    monthly_glob = sql_path(
        work_dir / "month_*.parquet"
    )

    # Totales ponderados.
    con.execute(
        f"""
        CREATE TEMP TABLE totals AS
        SELECT
            sensor_id,
            weekday,
            hour_slot,
            minute_slot,

            SUM(n_obs)::BIGINT AS n_obs,

            SUM(intensity_sum)
                / NULLIF(SUM(n_obs), 0)
                AS intensity_mean,

            SUM(occupancy_sum)
                / NULLIF(SUM(n_obs), 0)
                AS occupancy_mean,

            SUM(n_speed_obs)::BIGINT
                AS n_speed_obs,

            SUM(speed_sum)
                / NULLIF(SUM(n_speed_obs), 0)
                AS speed_mean_kmh

        FROM read_parquet(
            '{monthly_glob}'
        )

        GROUP BY
            sensor_id,
            weekday,
            hour_slot,
            minute_slot;
        """
    )

    # Frecuencias marginales de intensidad.
    con.execute(
        f"""
        CREATE TEMP TABLE intensity_freq AS
        SELECT
            sensor_id,
            weekday,
            hour_slot,
            minute_slot,
            traffic_intensity AS value,
            SUM(n_obs)::BIGINT AS freq

        FROM read_parquet(
            '{monthly_glob}'
        )

        GROUP BY
            sensor_id,
            weekday,
            hour_slot,
            minute_slot,
            traffic_intensity;
        """
    )

    # Frecuencias marginales de ocupación.
    con.execute(
        f"""
        CREATE TEMP TABLE occupancy_freq AS
        SELECT
            sensor_id,
            weekday,
            hour_slot,
            minute_slot,
            sensor_occupancy AS value,
            SUM(n_obs)::BIGINT AS freq

        FROM read_parquet(
            '{monthly_glob}'
        )

        GROUP BY
            sensor_id,
            weekday,
            hour_slot,
            minute_slot,
            sensor_occupancy;
        """
    )

    # Acumulados para cuantiles empíricos exactos.
    con.execute(
        """
        CREATE TEMP TABLE intensity_cum AS
        SELECT
            *,
            SUM(freq) OVER (
                PARTITION BY
                    sensor_id,
                    weekday,
                    hour_slot,
                    minute_slot
                ORDER BY value
                ROWS BETWEEN
                    UNBOUNDED PRECEDING
                    AND CURRENT ROW
            ) AS cum_freq,

            SUM(freq) OVER (
                PARTITION BY
                    sensor_id,
                    weekday,
                    hour_slot,
                    minute_slot
            ) AS total_freq

        FROM intensity_freq;
        """
    )

    con.execute(
        """
        CREATE TEMP TABLE occupancy_cum AS
        SELECT
            *,
            SUM(freq) OVER (
                PARTITION BY
                    sensor_id,
                    weekday,
                    hour_slot,
                    minute_slot
                ORDER BY value
                ROWS BETWEEN
                    UNBOUNDED PRECEDING
                    AND CURRENT ROW
            ) AS cum_freq,

            SUM(freq) OVER (
                PARTITION BY
                    sensor_id,
                    weekday,
                    hour_slot,
                    minute_slot
            ) AS total_freq

        FROM occupancy_freq;
        """
    )

    con.execute(
        """
        CREATE TEMP TABLE intensity_stats AS
        SELECT
            sensor_id,
            weekday,
            hour_slot,
            minute_slot,

            MIN(value) FILTER (
                WHERE cum_freq
                    >= CEIL(total_freq * 0.25)
            ) AS intensity_p25,

            MIN(value) FILTER (
                WHERE cum_freq
                    >= CEIL(total_freq * 0.50)
            ) AS intensity_median,

            MIN(value) FILTER (
                WHERE cum_freq
                    >= CEIL(total_freq * 0.75)
            ) AS intensity_p75

        FROM intensity_cum

        GROUP BY
            sensor_id,
            weekday,
            hour_slot,
            minute_slot;
        """
    )

    con.execute(
        """
        CREATE TEMP TABLE occupancy_stats AS
        SELECT
            sensor_id,
            weekday,
            hour_slot,
            minute_slot,

            MIN(value) FILTER (
                WHERE cum_freq
                    >= CEIL(total_freq * 0.25)
            ) AS occupancy_p25,

            MIN(value) FILTER (
                WHERE cum_freq
                    >= CEIL(total_freq * 0.50)
            ) AS occupancy_median,

            MIN(value) FILTER (
                WHERE cum_freq
                    >= CEIL(total_freq * 0.75)
            ) AS occupancy_p75

        FROM occupancy_cum

        GROUP BY
            sensor_id,
            weekday,
            hour_slot,
            minute_slot;
        """
    )

    # Salida final.
    con.execute(
        f"""
        COPY (
            SELECT
                t.sensor_id,
                t.weekday,

                t.hour_slot AS hour,
                t.minute_slot AS minute,

                t.n_obs,

                t.intensity_mean,
                i.intensity_median,
                i.intensity_p25,
                i.intensity_p75,

                t.occupancy_mean,
                o.occupancy_median,
                o.occupancy_p25,
                o.occupancy_p75,

                t.n_speed_obs,
                t.speed_mean_kmh

            FROM totals AS t

            LEFT JOIN intensity_stats AS i
            USING (
                sensor_id,
                weekday,
                hour_slot,
                minute_slot
            )

            LEFT JOIN occupancy_stats AS o
            USING (
                sensor_id,
                weekday,
                hour_slot,
                minute_slot
            )

            ORDER BY
                t.sensor_id,
                t.weekday,
                t.hour_slot,
                t.minute_slot
        )
        TO '{sql_path(output_path)}'
        (
            FORMAT PARQUET,
            COMPRESSION ZSTD
        );
        """
    )

    stats = con.execute(
        f"""
        SELECT
            COUNT(*) AS profile_rows,
            COUNT(
                DISTINCT sensor_id
            ) AS sensors,

            MIN(n_obs) AS min_n_obs,
            MEDIAN(n_obs) AS median_n_obs,
            MAX(n_obs) AS max_n_obs,

            SUM(n_obs) AS valid_observations,
            SUM(n_speed_obs) AS speed_observations

        FROM read_parquet(
            '{sql_path(output_path)}'
        );
        """
    ).fetchone()

    con.close()

    elapsed_all = (
        time.perf_counter()
        - started_all
    )

    print()
    print("=" * 76)
    print("DONE")
    print("=" * 76)
    print(
        f"Elapsed total:       "
        f"{elapsed_all / 60:.2f} min"
    )
    print(
        f"Profile rows:        "
        f"{stats[0]:,}"
    )
    print(
        f"Sensors:             "
        f"{stats[1]:,}"
    )
    print(
        "n_obs min/median/max:"
        f"{stats[2]:,} / "
        f"{stats[3]:,.1f} / "
        f"{stats[4]:,}"
    )
    print(
        f"Valid observations:  "
        f"{stats[5]:,}"
    )
    print(
        f"Speed observations:  "
        f"{stats[6]:,}"
    )
    print(
        f"Output size:         "
        f"{output_path.stat().st_size / 1024**2:.2f} MB"
    )
    print(
        f"Saved to:            "
        f"{output_path}"
    )

    if not args.keep_monthly:
        print(
            "Removing monthly work directory: "
            f"{work_dir}"
        )
        shutil.rmtree(
            work_dir,
            ignore_errors=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
