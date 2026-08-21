# Outputs and Results

[← Back to the main README](../README.md)

> This document contains the detailed technical documentation extracted from the main GLIMS README.

------------------------------------------------------------------------

## Outputs and Results

Each GLIMS experiment produces a structured output directory containing the main model results together with routing diagnostics, facility information, execution metadata, and validation artifacts.

The output structure is designed to support three complementary purposes:

``` text
Experiment outputs
        │
        ├── Model results
        │   ├── summary.csv
        │   ├── routes/
        │   └── facilities/
        │
        ├── Routing diagnostics
        │   └── routing/
        │
        └── Validation and reproducibility
            ├── config.json
            ├── metadata.json
            ├── neighborhood_status.csv
            └── audit/
```

The main `summary.csv` file therefore represents only one level of the experiment output. Detailed routing, facility, optimisation, and integrity information is stored separately so that model comparison and execution validation remain distinguishable.

------------------------------------------------------------------------

### Experiment Output Directory

Each simulation creates an experiment-specific directory containing both aggregated results and, when multiple zones are evaluated, zone-level outputs.

A typical structure is:

``` text
<experiment_id>/
│
├── config.json
├── metadata.json
├── neighborhood_status.csv
├── summary.csv
│
├── routes/
│   ├── m1_routes.csv
│   ├── m2_routes.csv
│   ├── m3_routes.csv
│   ├── m4_routes.csv
│   ├── m5_routes.csv
│   └── route_stops.csv
│
├── routing/
│   └── routing_plan_metrics.csv
│
├── facilities/
│   └── facility_summary.csv
│
├── audit/
│   ├── routing_integrity_summary.csv
│   ├── route_customer_summary.csv
│   ├── unroutable_customers.csv
│   └── performance_profile.csv
│
└── neighborhoods/
    ├── <zone_1>/
    │   ├── metadata.json
    │   ├── summary.csv
    │   ├── routes/
    │   ├── routing/
    │   ├── facilities/
    │   └── audit/
    │
    ├── <zone_2>/
    │   └── ...
    │
    └── ...
```

The exact set of generated files depends on the options enabled under the `output` block of the experiment configuration.

For experiments involving several neighborhoods or administrative zones, GLIMS preserves the detailed results associated with each zone while also producing experiment-level outputs.

This separation makes it possible to analyse results at different levels:

``` text
Experiment
    │
    ├── Overall results
    │
    └── Neighborhoods
        ├── Zone 1
        ├── Zone 2
        └── ...
```

------------------------------------------------------------------------

### Summary Results

The principal output for comparing the logistics models is:

``` text
summary.csv
```

This file contains the aggregated indicators generated for the evaluated models.

Conceptually:

``` text
                         summary.csv
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
       Operational       Environmental       Economic
        indicators        indicators         indicators
```

Depending on the evaluated model and available outputs, the summary includes indicators such as:

``` text
packages served
distance travelled
number of trips
CO₂ emissions
NOx emissions
route operating costs
facility service costs
other model-specific costs
total costs
cost per package
```

Representative fields include:

``` text
paquetes
km_recorridos
numero_viajes

emisiones_co2_kg
emisiones_nox_kg

costo_operacion_ruta_eur
costo_servicio_facility_eur
otros_costos_eur
costo_total_eur
costo_por_paquete_eur
```

The summary is intended to provide the main comparison layer across M1–M5.

For example:

``` text
                   M1   M2   M3   M4   M5
                    │    │    │    │    │
Distance ───────────┼────┼────┼────┼────┤
Trips ──────────────┼────┼────┼────┼────┤
CO₂ ────────────────┼────┼────┼────┼────┤
NOx ────────────────┼────┼────┼────┼────┤
Total cost ─────────┼────┼────┼────┼────┤
Cost/package ───────┼────┼────┼────┼────┤
```

The exact interpretation of an indicator depends on the corresponding logistics model.

For this reason, `summary.csv` should be used together with the methodological definitions presented under **Logistics Models**, rather than interpreting similarly named indicators as necessarily representing identical physical operations across M1–M5.

------------------------------------------------------------------------

### Route-Level Results

Detailed vehicle routes are stored under:

``` text
routes/
```

with model-specific files such as:

``` text
m1_routes.csv
m2_routes.csv
m3_routes.csv
m4_routes.csv
m5_routes.csv
```

These files provide a route-level representation of the operations that produced the aggregated values reported in `summary.csv`.

The relationship is:

``` text
Individual routes
       │
       ▼
Model-level aggregation
       │
       ▼
summary.csv
```

Route-level outputs are useful when the aggregate result alone is insufficient to understand why two logistics models behave differently.

For example, two models may produce similar total distance but substantially different:

- numbers of routes;
- route lengths;
- vehicle utilisation;
- customer distributions;
- service patterns; or
- transport-mode compositions.

The route files therefore support detailed diagnostics and spatial or operational analyses beyond the high-level model comparison.

When route geometry output is enabled, route-level information can additionally include the geometry returned by OSRM, allowing the generated routes to be reconstructed or visualised geographically.

------------------------------------------------------------------------

### Route Stops

When enabled, individual stops are stored in:

``` text
routes/route_stops.csv
```

This file represents the internal sequence of stops associated with the generated routes.

Its granularity differs from the model route files:

``` text
m*_routes.csv
      │
      └── one record describes a route

route_stops.csv
      │
      └── records describe individual stops
          belonging to those routes
```

The stop information can include:

``` text
route identifier
stop position
customer / stop identifier
coordinates
assigned load
arrival time
service start time
service end time
```

Temporal fields such as:

``` text
arrival_datetime
service_start_datetime
service_end_datetime
```

make it possible to reconstruct the progression of a route over the simulated operating period.

Conceptually:

``` text
Route
  │
  ├── Stop 1
  │    ├── arrival
  │    ├── service start
  │    └── service end
  │
  ├── Stop 2
  │    ├── arrival
  │    ├── service start
  │    └── service end
  │
  └── ...
```

This output is particularly useful for temporal analysis, route visualisation, and detailed verification of the simulated operations.

------------------------------------------------------------------------

### Routing Plan Metrics

Routing-algorithm diagnostics are stored separately from the logistics-model results under:

``` text
routing/routing_plan_metrics.csv
```

This distinction is important.

`summary.csv` answers:

> **What operational, environmental, and economic outcome did the logistics model produce?**

whereas `routing_plan_metrics.csv` helps answer:

> **How did the routing algorithm construct or improve the routes used by that model?**

The routing metrics can include fields such as:

``` text
routing_runtime_seconds

initial_distance_km

cws_initial_distance_km
cws_initial_route_count

ils_final_distance_km
ils_final_route_count

ils_improvement_km
ils_improvement_percent

ils_runtime_seconds
ils_iterations_completed
ils_iterations_without_improvement
```

For ILS experiments, this allows the optimisation process to be evaluated independently from the final logistics-model indicators.

For example:

``` text
CWS initial solution
        │
        ├── distance
        └── route count
        │
        ▼
       ILS
        │
        ├── runtime
        ├── iterations
        └── improvement
        │
        ▼
Final routing solution
```

The resulting metrics can be used to evaluate whether the additional computational effort introduced by ILS produces meaningful routing improvements.

This distinction is particularly useful when comparing CWS and ILS experiments because optimisation quality and logistics-model performance are related but are not the same analytical question.

------------------------------------------------------------------------

### Facility Results

Facility-related outputs are stored under:

``` text
facilities/
```

with:

``` text
facility_summary.csv
```

providing information about the facilities involved in the simulated logistics models.

These outputs are primarily relevant to models that depend on intermediate logistics infrastructure, such as PUDO points or microhubs.

Conceptually:

``` text
Demand
   │
   ▼
Facility assignment
   │
   ▼
Selected / used facilities
   │
   ▼
facility_summary.csv
```

The facility results make it possible to inspect which infrastructure elements were involved in the simulation and how demand was associated with them.

They complement the model-level indicators in `summary.csv`.

For example, an aggregate model result may indicate a change in route distance or total cost, while the facility output can help identify the infrastructure configuration associated with that result.

The methodology governing facility selection and assignment is described under **Logistics Models** and the corresponding preprocessing sections.

------------------------------------------------------------------------

### Experiment Metadata

Each experiment can store execution metadata in:

``` text
metadata.json
```

This file records information about the experiment execution and its provenance.

The generated metadata includes information such as:

``` text
git_commit
git_branch
dirty_worktree

config_hash

demand_source_file
demand_file_hash

python_version
platform

runtime_seconds
status
```

The stored hashes make possible to distinguish files that happen to share the same filename but do not contain the same content.

Together with the experiment configuration, this provides a record of the computational conditions associated with a particular result.

------------------------------------------------------------------------

#### Resolved Configuration

When configuration saving is enabled, GLIMS stores:

``` text
config.json
```

inside the experiment output directory.

This file represents the configuration associated with the executed experiment.

It is particularly important when command-line overrides are used.

Conceptually:

``` text
Original JSON
      │
      +
CLI overrides
      │
      ▼
Resolved experiment configuration
      │
      ▼
config.json
```

Therefore, the output configuration should be preserved together with the experiment results.

A result directory containing only the numerical outputs but not its associated configuration and metadata provides substantially less information for later reproduction or auditing.

------------------------------------------------------------------------

### Neighborhood Execution Status

For multi-zone experiments, GLIMS records zone-level execution information in:

``` text
neighborhood_status.csv
```

This file provides a compact view of whether each requested zone completed successfully.

This distinction matters because a multi-zone experiment may encounter a problem in one zone while other zones still produce valid outputs.

Conceptually:

``` text
Experiment
   │
   ├── Zone A ─ completed
   ├── Zone B ─ completed
   ├── Zone C ─ failed
   └── Zone D ─ completed
```

The presence of an experiment-level output directory should therefore not automatically be interpreted as evidence that every requested zone completed successfully.

`neighborhood_status.csv` should be inspected before aggregating or comparing multi-zone results.

------------------------------------------------------------------------

### Routing Integrity and Audit Outputs

GLIMS separates model results from routing-validation information.

The audit directory can contain:

``` text
audit/
├── routing_integrity_summary.csv
├── route_customer_summary.csv
├── unroutable_customers.csv
├── osrm_snapping_audit.csv
└── performance_profile.csv
```

These files should be used to verify that the routing solution underlying the model indicators is internally consistent.

#### OSRM Snapping and Last-Meter Access

`audit/osrm_snapping_audit.csv` stores the original demand coordinate, the
OSRM-snapped waypoint, `snap_distance_m`, the routing profile, and route
assignment identifiers when available. Snapping remains auditable regardless
of whether the last-meter penalty is enabled.

When `last_meter_access` is enabled, additional fields are also propagated to
route and stop outputs. Relevant fields include:

| Field | Meaning |
|:--|:--|
| `network_km` | Distance represented directly on the OSRM network at model-summary level. |
| `last_meter_access_km` | Explicit off-network customer-access distance added by the configured policy. |
| `last_meter_access_time_min` | Time associated with that access distance. |
| `km_recorridos` | System distance including the network distance and enabled last-meter access. |
| `network_distance_km` | OSRM network distance for an individual route. |
| `system_distance_km` | Route network distance plus explicit last-meter access distance. |
| `base_stop_service_min` | Base customer service time before the access adjustment. |
| `stop_service_min` | Effective route-level service/access time after the adjustment. |
| `snap_distance_m` | One-way displacement between the original coordinate and OSRM waypoint for an individual stop. |
| `last_meter_access_distance_m` | Access distance actually applied to the stop; for round-trip mode this is twice `snap_distance_m`. |
| `effective_service_time_min` | Base stop service plus applied access time for customer-delivery legs. |

Vehicle operating-distance costs and tailpipe emissions continue to use the
OSRM network distance rather than treating walking access as vehicle travel.
Labour duration does include the additional access time because the adjustment
is part of the route timeline.

For M5, when the feature is explicitly enabled, customer access is added to the
customer round-trip distance/time; no artificial 5-minute delivery service is
introduced because M5 represents customer collection rather than home
delivery.

------------------------------------------------------------------------

------------------------------------------------------------------------

#### Routing Integrity Summary

The principal integrity check is stored in:

``` text
routing_integrity_summary.csv
```

The validation logic compares the demand expected to participate in routing with the demand actually represented in the resulting routes.

At customer level:

``` text
Input customers
       │
       ├── minus excluded / unroutable customers
       │
       ▼
Expected routable customers
       │
       ▼
Compare with customers assigned to routes
```

Conceptually:

$$
N_{\text{expected}} = N_{\text{input}} - N_{\text{excluded}}
$$

and the resulting value is compared against the unique customers assigned to the routing solution.

The audit also checks for inconsistencies such as:

``` text
unassigned customers
duplicated customers
unexpected customer IDs
```

A similar reconciliation is performed for parcel counts.

This is important because an apparently favourable result in `summary.csv` may be misleading if part of the expected demand was accidentally omitted or duplicated during routing.

------------------------------------------------------------------------

#### Route-Customer Summary

The file:

``` text
route_customer_summary.csv
```

provides additional information linking routed customers to the generated route structure.

It can be used when the aggregate integrity result indicates a discrepancy or when a more detailed inspection of customer assignment is required.

The distinction is:

``` text
routing_integrity_summary.csv
        │
        └── Is the routing solution consistent?

route_customer_summary.csv
        │
        └── How are customers represented
            within the routing solution?
```

This makes the audit outputs useful both as automated validation artifacts and as diagnostic information when an experiment behaves unexpectedly.

------------------------------------------------------------------------

#### Unroutable Customers

Customers that cannot be incorporated into a valid routing problem can be recorded in:

``` text
unroutable_customers.csv
```

An unroutable customer should not automatically be treated as a software error.

Depending on the routing instance, a customer may be impossible to serve under the configured constraints or network conditions.

Examples can include situations where:

``` text
customer demand > vehicle capacity
```

or where the network and active route-duration constraints make even an individual:

``` text
Depot → Customer → Depot
```

trip infeasible.

The routing-integrity process distinguishes these explicitly excluded customers from customers that were unexpectedly lost during routing.

This distinction is essential:

``` text
Explicitly unroutable
        ≠
Unexpectedly unassigned
```

The former may represent a valid consequence of the experiment assumptions; the latter may indicate a routing or data-integrity problem that requires investigation.

------------------------------------------------------------------------

### Performance Profiling

When performance profiling is enabled, execution information can be stored in:

``` text
audit/performance_profile.csv
```

This output is intended for computational diagnostics rather than logistics-model comparison.

It can be used to identify expensive stages of an experiment and investigate differences in runtime across:

- cities;
- demand sizes;
- logistics models;
- routing algorithms; or
- optimisation settings.

Performance profiling should therefore be interpreted separately from operational indicators such as distance, emissions, or cost.

A faster algorithm is not necessarily associated with a better logistics solution, and a better routing solution may require additional computational effort.

------------------------------------------------------------------------

### Successful, Partial, and Failed Experiments

Experiment execution and result validity should be treated as separate concepts.

At experiment level, execution metadata may distinguish states such as:

``` text
completed
partial
failed
```

A conceptual interpretation is:

``` text
completed
    │
    └── requested execution completed

partial
    │
    └── some requested components/zones completed,
        while others failed

failed
    │
    └── experiment could not produce the
        required execution
```

A `partial` experiment should not automatically be aggregated or compared with a fully completed experiment without first determining which components are missing.

Likewise, the presence of `summary.csv` does not by itself prove that the corresponding routing solution passed all integrity checks.

------------------------------------------------------------------------

### Validating an Experiment Before Analysis

The recommended validation sequence is:

``` text
Experiment finished
        │
        ▼
1. Check metadata.json
        │
        ▼
Experiment status acceptable?
        │
        ▼
2. Check neighborhood_status.csv
        │
        ▼
All required zones completed?
        │
        ▼
3. Check routing_integrity_summary.csv
        │
        ▼
Expected demand reconciled?
        │
        ▼
4. Inspect unroutable_customers.csv
        │
        ▼
Exclusions understood?
        │
        ▼
5. Inspect routing diagnostics if required
        │
        ▼
6. Analyse summary.csv
```

The main principle is:

> **Result files should be validated before they are interpreted.**

In particular, a low distance, low cost, or low emissions value should not be interpreted as an improvement until it has been confirmed that the expected demand was actually served.

------------------------------------------------------------------------

### Choosing the Appropriate Output Level

Different research questions require different output files.

| Research question                        | Primary output                        |
|:-----------------------------------------|:--------------------------------------|
| How do M1–M5 compare overall?            | `summary.csv`                         |
| How many routes were generated?          | `summary.csv` / model route files     |
| What does an individual route look like? | `routes/m*_routes.csv`                |
| Which customers belong to a route?       | `routes/route_stops.csv`              |
| When are customers reached and served?   | `routes/route_stops.csv`              |
| Which facilities were used?              | `facilities/facility_summary.csv`     |
| How much did ILS improve CWS?            | `routing/routing_plan_metrics.csv`    |
| How long did routing take?               | `routing/routing_plan_metrics.csv`    |
| Did all zones complete?                  | `neighborhood_status.csv`             |
| Was all expected demand routed?          | `audit/routing_integrity_summary.csv` |
| Which customers could not be routed?     | `audit/unroutable_customers.csv`      |
| Which execution produced these results?  | `metadata.json` + `config.json`       |
| Where was computational time spent?      | `audit/performance_profile.csv`       |

This layered structure allows GLIMS results to be analysed without requiring every experiment to be interpreted from the most detailed route-level data.

------------------------------------------------------------------------

### Recommended Analysis Workflow

For comparative experiments, the recommended workflow is:

``` text
                 Experiment outputs
                        │
                        ▼
              Validate execution
                        │
                        ▼
              Validate routing
                        │
                        ▼
              Confirm demand balance
                        │
                        ▼
                 summary.csv
                        │
             ┌──────────┴──────────┐
             │                     │
             ▼                     ▼
      Model comparison       Unexpected result?
                                   │
                                   ▼
                          routing_plan_metrics
                                   │
                                   ▼
                              route details
                                   │
                                   ▼
                          facility / audit data
```

The high-level summary should therefore be the starting point for model comparison, while the detailed outputs provide progressively deeper levels of explanation.

For example, if an experiment shows:

``` text
M4 → lower total distance than M3
```

the analysis can move from:

``` text
summary.csv
```

to:

``` text
route counts
        │
        ▼
individual routes
        │
        ▼
facility assignments
        │
        ▼
routing diagnostics
```

to determine which operational changes produced the difference.

Similarly, when comparing CWS and ILS, the model-level outcome should be considered together with:

``` text
ILS improvement
        +
ILS runtime
        +
final route count
```

rather than evaluating the optimisation algorithm exclusively from its final model cost.

------------------------------------------------------------------------

### Reproducibility of Results

A GLIMS result should ideally be preserved together with:

``` text
Results
   │
   ├── summary.csv
   ├── detailed outputs
   │
   ├── config.json
   ├── metadata.json
   │
   ├── routing validation
   └── execution status
```

This separation between results, diagnostics, and provenance is intentional.

It allows `summary.csv` to remain convenient for large-scale comparative analysis while preserving the information required to investigate, validate, and reproduce the experiment from which each summary row originated.

------------------------------------------------------------------------
