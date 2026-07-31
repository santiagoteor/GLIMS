- Where are the sources by various number in the project? 
    + 1.15 bybicle: *It is not neccesary, we don't have to put a something like this*
    + emissions (220 g CO₂/km diesel, 0 for electric/bike, 25 g/km customer; unreferenced and "0" for electric is tank-to-wheel.): *could be like this, TTW is a good aproximation*
    + operating costs (€/km, €/h, 0.50€ PUDO fee, 45€/day microhub fixed are unsupported assumptions.)

- Travels from a CC to a microhub or similar are made with electric van as is explained in the technical documentation (205778_annex_2_-_descripcio_del_projecte_def -- pag. 3)? Is realistic? *Yes, that way is the correct one*
- Is the group of institutions searching a simple model (squeare neighbourhoods, simple values for costs, emissions...) or a more complex solution? *a more complex one, if we can put the actual form of a neighbourhood, we should add it*
- How flexible is the utilization of Clarke-Wright + ILS, explained in the technical documentation (205778_annex_2_-_descripcio_del_projecte_def -- pag. 5)? Can we use it as a base of a more complex or different algorithm? *Yes, this is possible*
- Is it necessary the use of dynamic density of population and others factors in the problem, as It is mentioned in technical documentation (205778_annex_2_-_descripcio_del_projecte_def -- pag. 3 / PR3yPR4)? Could be resolved using Montecarlo or stocasthics methods. *Could be a possibility doing it more complex, but not a priority, with a simple dymanic system by neighboorhood its okay*
- Is it necessary the use of the 10 indicators mentioned in the technical documentation (205778_annex_2_-_descripcio_del_projecte_def -- pag. 3)? 
- Where is the source for the .csv data used as raw data in the project (B2C, CC... )?
- Have the notebook called "Barcelona" any actual use? *No, it's not*
- Is a good practise convert to numeric latitude and longitude data? *No*
- Use or pass information of parameters and similar variables to a .csv is a good practise? 
- Only is used CO2 as pollulant? *CO2. NOx and particles*
- Can we take a model from amazon vehicles? *We should do it, because vans are very similar, bicycles and human transport don't have this lucky*



######################### SECOND MEETING ################################

- Should we assume that PUDO and hubs are located on the centroids? Or we have to located it in a more likely place? (car parks, markets...)
- Should we use PRISMA methodology to related-work section on the article?
- Is it necessary the use of the 10 indicators mentioned in the technical documentation (205778_annex_2_-_descripcio_del_projecte_def -- pag. 3)? 
- Where is the source for the .csv data used as raw data in the project (B2C, CC... )?
- Are you familiar with OSRM? Do you see use it as a good practise? 
- Have we to use variations in density and demand in the problem? 
- Should we have things in mind like how to test it, if we have to validate with something... 
- How should be an MVP for this project and when it has to be ready? 
