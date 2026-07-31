**This document aims to establish a methodology for identifying, estimating, and justifying the main parameters used in the project. Since the current implementation is based on a simplified MVP, several parameters were initially introduced as assumptions. The objective is to replace or support these assumptions with references from the literature, technical reports, manufacturer specifications, or, when necessary, well-documented hypotheses.**

*Vehicle capacities (cargo bike, van, pedestrian)*
- The preferred approach is to perform a literature review, as similar studies on last-mile logistics often report vehicle capacities or cite their original sources.
- When a specific vehicle model is considered, manufacturer specifications should be used, as they provide the most reliable information regarding payload and package capacity.
- Could be better use an Anazon model for the van, bike and pedestrian could be more difficult

*CO₂ emission factors (diesel and electric vehicles)*
- Emission factors should be obtained from official or widely accepted sources, such as the EMEP/EEA Emission Inventory Guidebook, national environmental agencies, or other recognized databases.
- For electric vehicles, assuming 0 g CO₂/km is only valid under a Tank-to-Wheel (TTW) perspective, where no emissions occur during vehicle operation. If a Well-to-Wheel (WTW) or life-cycle perspective is adopted, the electricity generation mix should also be considered. Therefore, the selected assumption must be clearly stated and justified.

*Bicycle correction factor*
- If no reference exists for the correction factor (currently 1.15), it can be modeled as a calibration parameter, denoted by a coefficient such as β.
- A sensitivity analysis can then be performed by evaluating several plausible values of β to determine whether the conclusions remain stable. If the results are robust across the tested range, the exact value of the parameter becomes less critical.
- Nevertheless, this should be considered a temporary solution. Whenever possible, the factor should be validated using published literature or empirical routing data.

*PUDO, microhub and pedestrian-related costs*
- These costs are often difficult to obtain directly from the literature.
- A reasonable approach is to estimate them by decomposing the total cost into its main components, such as rental costs, operating expenses, labour costs, or service fees, using publicly available market data.
- Another common practice is to adopt values reported in similar academic studies and clearly state them as scenario assumptions.
- Whenever possible, these parameters should also be included in a sensitivity analysis to assess how strongly they influence the model's results.
- One possible solution is using ~Observatorio de Costes del Transporte de Mercancías por Carretera (Ministerio de Transportes y Movilidad Sostenible)~ to the variable cost of the vehicle and 
~Observatorio de Costes (partida "personal de conducción")~ to the variable cost of workers in a motor vehicle.  
