# Running GLIMS

[← Back to the main README](../README.md)

> This document contains the detailed technical documentation extracted from the main GLIMS README.

------------------------------------------------------------------------

## Running GLIMS

Once the required datasets, demand instances, OSRM services, and experiment configuration are available, GLIMS experiments are executed through the main simulation entry point:

``` text
code/simulation/osrm_simulator.py
```

The simulator should normally be launched from the root directory of the repository using the module interface:

``` bash
python -m code.simulation.osrm_simulator
```

The general execution workflow is:

``` text
Prepared city data
        +
Generated demand instance
        +
Running OSRM services
        +
Experiment configuration
        │
        ▼
python -m code.simulation.osrm_simulator
        │
        ▼
Load and resolve experiment settings
        │
        ▼
Prepare study area and facilities
        │
        ▼
Load selected demand instance
        │
        ▼
Construct routing problems
        │
        ▼
      CWS / ILS
        │
        ▼
      M1–M5
        │
        ▼
Routing integrity checks
        │
        ▼
Results + metadata
```

The methodological meaning of these stages is described in the preceding sections. This section focuses on how experiments are executed in practice.

------------------------------------------------------------------------

### Before Running an Experiment

A standard GLIMS experiment requires four main components to be available:

``` text
1. Python environment
2. Required input datasets
3. Generated demand instance
4. Running OSRM services
```

The Python virtual environment should first be activated.

**Windows — PowerShell**

``` powershell
.\.venv\Scripts\Activate.ps1
```

**Linux**

``` bash
source .venv/bin/activate
```

The terminal should normally display:

``` text
(.venv)
```

when the environment is active.

The required city-level datasets and classified logistics facilities must also have been prepared according to the **Data and Processing Pipeline** section.

The selected demand instance must exist under:

``` text
results/<city>/demand/
```

For example:

``` text
results/madrid/demand/demand_low_40000_seed_42.csv
```

Finally, the required OSRM services must be running.

They can be checked with:

``` bash
docker ps
```

A complete experiment may use more than one transport profile because M1–M5 contain different distribution legs. Therefore, the `driving`, `cycling`, and `walking` services prepared for the selected city should normally be available before executing the complete model comparison.

------------------------------------------------------------------------

### Checking the Simulator Interface

The currently supported command-line options can be inspected with:

``` bash
python -m code.simulation.osrm_simulator --help
```

The interface currently exposes options related to:

- experiment configuration;
- city and study zones;
- demand scenario and instance selection;
- OSRM routing profile;
- CWS and ILS;
- ILS search parameters and random seed;
- route-geometry export;
- traffic-related experimental settings;
- simulation date and shift definition.

------------------------------------------------------------------------

### Running an Experiment from a Configuration File

The recommended way to execute a reproducible GLIMS experiment is:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json
```

For example:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json
```

On PowerShell, the same command can be written as:

``` powershell
python -m code.simulation.osrm_simulator `
  --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

or simply:

``` powershell
python -m code.simulation.osrm_simulator --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

When a configuration file is supplied, it defines the main experimental conditions described under **Experiment Configuration**, including the study area, demand selection, routing method, facility behaviour, output settings, and other simulation parameters.

Conceptually:

``` text
configs/experiments/<experiment>.json
                 │
                 ▼
          osrm_simulator.py
                 │
                 ▼
      Resolve experiment settings
                 │
                 ▼
          Execute experiment
```

Keeping these settings in configuration files is preferable to maintaining long experiment-specific commands because the complete experimental definition can be stored under version control and reused later.

------------------------------------------------------------------------

### Running Batch Experiments from a Configuration File

A configuration file may define multiple demand scenarios and/or instance sizes. GLIMS expands these values into independent experiment configurations before simulation.

For example:

``` json
{
  "demand_scenario": ["low", "medium", "high"],
  "instance_size": [2000, 8000],
  "demand_seed": [42, 101, 202]
}
```

The scenario and size dimensions form a Cartesian product. Seed replication is then applied to every scenario-size combination:

``` text
3 scenarios × 2 sizes × 3 demand-seed replicates
= 18 experiments
```

The simulator executes the expanded experiments sequentially and reports the active scenario, instance size, demand seed, and ILS seed for each run. Each expanded run remains an independent GLIMS experiment with its own resolved configuration and outputs.

The seed-expansion rules are unchanged. In particular, if both `demand_seed` and `ils_random_seed` are lists, the seeds are paired one-to-one by position rather than expanded as a Cartesian product.

Batch scenario and size lists are defined in the JSON configuration. The CLI options `--demand-scenario` and `--instance-size` are scalar overrides and are intended for individual executions or temporary tests.

------------------------------------------------------------------------

### Running with Command-Line Overrides

Selected experiment settings can also be supplied through command-line arguments.

This makes it possible to reuse an existing configuration while temporarily changing a specific experimental condition.

For example, an experiment configured to use ILS can be executed with CWS instead using:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json \
  --routing-algorithm cws
```

Similarly, the city can be overridden with:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --city madrid
```

and the simulation zones with:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --zones Embajadores
```

Multiple zones can be supplied after `--zones` when required.

Other currently exposed overrides include parameters such as:

``` text
--demand-scenario
--instance-size
--demand-seed
--demand-instance-id
--profile
--routing-algorithm
--cws-allow-route-reversal
--ils-max-iterations
--ils-max-no-improvement
--ils-perturbation-moves
--ils-biased-cws-alpha-min
--ils-biased-cws-alpha-max
--ils-restricted-relocate
--no-ils-restricted-relocate
--ils-relocate-candidate-fraction
--ils-relocate-neighbor-routes
--ils-relocate-max-insertions
--ils-random-seed
--save-route-geometry
--traffic-profile
--traffic-multiplier
--simulation-date
--shift-start
--shift-duration-min
--time-traffic-profile
```

The authoritative list for the installed version of GLIMS should always be obtained with:

``` bash
python -m code.simulation.osrm_simulator --help
```

This avoids duplicating the complete parameter documentation already provided under **Experiment Configuration** while keeping the execution interface discoverable.

------------------------------------------------------------------------

### Selecting the Demand Instance

Demand generation and logistics simulation are intentionally separate stages.

An experiment therefore consumes a previously generated demand instance rather than generating a new customer population during execution.

The usual selection is based on:

``` text
city
+
demand_scenario
+
instance_size
+
demand_seed
```

which corresponds to a file following the convention:

``` text
demand_<scenario>_<size>_seed_<seed>.csv
```

For example:

``` text
city            = madrid
demand_scenario = low
instance_size   = 40000
demand_seed     = 42
```

These values are normally defined in the experiment configuration. When `demand_scenario` or `instance_size` is a list, each expanded experiment resolves its own concrete demand file using the corresponding scenario, size, and demand seed.

They can also be overridden from the command line with scalar values:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --demand-scenario low \
  --instance-size 40000 \
  --demand-seed 42
```

An explicit demand instance can instead be selected using:

``` text
--demand-instance-id
```

Because this option identifies one concrete demand instance, it cannot be combined with list-valued `demand_scenario` or `instance_size` in a batch configuration.

This option accepts the corresponding demand CSV filename or stem inside:

``` text
results/<city>/demand/
```

Reusing the same demand instance is important when comparing alternative logistics or routing configurations because it prevents changes in customer locations or parcel assignments from being introduced simultaneously with the experimental change being studied.

------------------------------------------------------------------------

### Selecting CWS or ILS

The routing strategy is normally defined by the `routing.algorithm` setting in the experiment configuration.

The two currently supported values are:

``` text
cws
ils
```

A routing method can also be selected explicitly from the command line.

#### CWS

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --routing-algorithm cws
```

In this case, Clarke-Wright Savings constructs the routing solution directly.

#### ILS

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --routing-algorithm ils
```

ILS uses the CWS-based construction and subsequently applies the improvement procedures described under **Routing Algorithms**.

Individual ILS settings can also be overridden.

For example:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json \
  --routing-algorithm ils \
  --ils-max-iterations 500 \
  --ils-max-no-improvement 100 \
  --ils-random-seed 42
```

------------------------------------------------------------------------

### Running on Windows and Linux

The Python entry point is platform-independent.

The main difference between Windows PowerShell and Linux shells is the syntax used to continue a command across multiple lines.

#### Windows — PowerShell

PowerShell uses the backtick:

``` powershell
python -m code.simulation.osrm_simulator `
  --config .\configs\experiments\madrid_embajadores_ils_baseline.json
```

#### Linux

Linux shells such as Bash use the backslash:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/madrid_embajadores_ils_baseline.json
```

Both can also execute the command on a single line:

``` bash
python -m code.simulation.osrm_simulator --config configs/experiments/madrid_embajadores_ils_baseline.json
```

Repository-relative paths are recommended so that experiment commands remain portable between machines.

------------------------------------------------------------------------

### Running Multiple Experiments Sequentially

Large experimental campaigns commonly require several configuration files to be executed one after another.

On Linux, multiple experiments can be chained with `&&`:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/experiment_01.json && \
python -m code.simulation.osrm_simulator \
  --config configs/experiments/experiment_02.json && \
python -m code.simulation.osrm_simulator \
  --config configs/experiments/experiment_03.json
```

Using `&&` means that the next experiment starts only when the preceding command terminates successfully.

This behaviour is useful for controlled experiment batches because an execution failure prevents the remaining chain from continuing silently.

For a larger collection of experiments, a Bash loop can be used:

``` bash
for config in \
  configs/experiments/experiment_01.json \
  configs/experiments/experiment_02.json \
  configs/experiments/experiment_03.json
do
    echo "Running $config"
    python -m code.simulation.osrm_simulator --config "$config" || break
done
```

The `|| break` condition stops the sequence if one experiment returns an error.

A similar PowerShell workflow can be written as:

``` powershell
$configs = @(
    ".\configs\experiments\experiment_01.json",
    ".\configs\experiments\experiment_02.json",
    ".\configs\experiments\experiment_03.json"
)

foreach ($config in $configs) {
    Write-Host "Running $config"

    python -m code.simulation.osrm_simulator --config $config

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Experiment failed. Stopping batch."
        break
    }
}
```

For systematic experimental campaigns, maintaining separate JSON configurations is preferable to embedding large numbers of parameter combinations directly into shell commands.

------------------------------------------------------------------------

### Experiment Completion and Failure

An experiment should not be considered valid solely because the simulator produced result files.

The complete execution must also be evaluated through the routing-integrity and experiment-status information generated by GLIMS.

Conceptually:

``` text
Simulation finished
        │
        ▼
Were outputs generated?
        │
        ▼
Did the experiment complete successfully?
        │
        ▼
Do routing-integrity checks pass?
        │
        ▼
Are expected customers / packages represented?
        │
        ▼
Results can be interpreted
```

The generated validation and audit artifacts are described in **Outputs and Results**.

------------------------------------------------------------------------
------------------------------------------------------------------------

### Running with Last-Meter Access

Last-meter access is configured in the experiment JSON rather than through a
separate preprocessing command. A typical M1/M2 configuration is:

``` json
"last_meter_access": {
  "enabled": true,
  "walking_speed_m_s": 1.2,
  "round_trip": true,
  "models": ["M1", "M2"]
}
```

The experiment is then run with the usual command:

``` bash
python -m code.simulation.osrm_simulator \
  --config configs/experiments/<experiment>.json
```

No additional OSRM call is required specifically for the penalty. To apply the
mechanism to all supported customer-facing models, set `models` to `null` or
`[]`. To preserve the historical baseline, leave `enabled` as `false`.

