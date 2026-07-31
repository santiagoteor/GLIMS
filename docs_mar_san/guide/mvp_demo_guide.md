# GLIMS — Phase 1 MVP Demo Guide

A reproducible demo that compares the five last-mile configurations (M1–M5) over
real street-network data and produces the KPIs committed in the project memoria.
It does not wait for CWS, ILS, or the full LRP (OSMR yes). Its value is the end-to-end pipeline and the comparison,
not route optimality.

---

## 1. Purpose

Show, in a single run, that the simulation engine works end to end on real
geography and already answers the location/routing research questions at a
**demonstrative** level: for a given neighborhood, which of the five
configurations wins on cost, emissions, and congestion.

This is early evidence that validates the approach and provides the base on which
Level A/B/C increments will be built. It is **not** the paper deliverable.

---

## 2. Scope

**In scope**
- At minimum, **one neighborhood executed end to end** (currently one for Barcelona). 
- All five models: M1 (combustion baseline), M2 (electric van), M3
  (microhub + cargo bike), M4 (PUDO + on-foot delivery), M5 (PUDO + customer
  pickup). Could contain fake data. 
- Real OSM or OSMR routing and the committed KPIs.
- Use CWS

**Out of scope (name these as next steps, do not simulate them)**
- CWS / multi-start / ILS optimization.
- AI/ML demand or traffic prediction; stochastic/simheuristic components.

---

## 3. Inputs

| File | Content |
| --- | --- |
| `data/{city}/puntos_b2c.csv` | B2C delivery points (customers) with lat/lon |
| `data/{city}/centros_cc.csv` | Existing consolidation centers with lat/lon |
| `data/{city}/limites_barrios.csv` | Neighborhood bounding boxes |
| `data/parametros_modelos.csv` | Per-model params: capacity, cost/km, cost/hour, CO₂/km, PUDO fees |

The street network itself is fetched by OSMnx at runtime (with caching), not
stored in `data/`.

(and the data for the instances of demand, by Open data BCN)

---

## 4. Pipeline (end to end)

1. **Data preparation** — normalize and load the CSVs.
2. **Network load & node assignment** — download or use the drive network, snap
   customers and centers to nearest OSM nodes. We have to approximate distances if the strret is too much long
3. **Center selection** — pick the nearest existing consolidation center per
   neighborhood. 
5. **Model evaluation** — compute the five models (cost, CO₂, trips, km).
6. **Export** — write the results CSV; render comparison charts.

This is exactly the existing flow. The demo only makes it **clean, runnable, and
reproducible**.

---

## 5. How to run

```python
CIUDAD_ACTIVA = "barcelona"        # "madrid", "barcelona", or "valencia"
BARRIO_ACTIVO = "eixample"     # None = all neighborhoods / a name = just one
```

Results are written to:

```
results/resultados_{city}.csv            # when BARRIO_ACTIVO is None
results/resultados_{city}_{barrio}.csv   # when a single neighborhood is set
```

---

## 6. Outputs & KPIs

A **results CSV** (already generated) plus a **comparison view** per
neighborhood. For each *neighborhood × model*, the four KPIs from the memoria:

- **Kilometers traveled**, broken down by mode (van / bike / on foot).
- **Number of trips** per mode.
- **Total CO₂ emissions**.
- **Total system cost**.

The minimum "showable" visualization: a comparison table plus one chart per KPI,
so a viewer can read at a glance which model wins on each dimension within each
neighborhood.

---

## 7. What the demo demonstrates (RQ mapping)

Answers **PR1–PR4 demonstratively** — for a given neighborhood, "the most
efficient configuration on cost / emissions / congestion is X." It does **not**
answer them definitively (that needs Level C + optimization), but it proves the
machinery exists and produces coherent numbers over real geography.

| Research question | Demo status |
| --- | --- |
| PR1 (optimal number/location of facilities) | Demonstrative — nearest existing CC only; full answer needs Level C |
| PR2 (AI for optimal network config) | Not yet — needs Level C + ML |
| PR3 (urban variables / traffic in routing) | Not yet — needs the predictive layer |
| PR4 (e-commerce demand variability) | Not yet — needs the stochastic layer |

---

## 8. Success criteria

The demo is "ready to show" when:

1. It runs with **one command** and regenerates results without manual steps.
2. It produces the **four KPIs for all five models** in at least one full
   neighborhood.
3. The output is **readable** (table + chart), not a dump of debug `print`s.
4. It is **stated in one line** that internal routing uses a provisional
   nearest-neighbor heuristic and that CWS → ILS → LRP are the next increments.

---

## 9. Known limitations to state during the demo

Being explicit about these keeps the demo honest and pre-empts questions:

- **Depot = geometric centroid** of the neighborhood, not a real facility. This
  moves to real candidate sites at Level C.
- **`km_internos` does not scale with `numero_viajes`** (the Point-2 issue).
  Either fix it before the demo or label the routing KPIs as "heuristic baseline,
  pending CWS" — decide this before freezing (see §11).
- **No location optimization**: the demo selects the nearest existing CC; it does
  not choose where to place microhubs/PUDO.

---
