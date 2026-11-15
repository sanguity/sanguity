# Demo data

`generate.py` writes a **fictional** dataset so Sanguity can be developed, tested and shown without anyone's real
health records. The patient, providers, insurer, values and documents are all made up.

```sh
python3 -I demo/generate.py                    # writes www/data.json, www/settings.json, www/watch/daily.json, www/files/demo-report.pdf
python3 -I demo/generate.py --end 2026-11-14   # same data every time for a fixed end date
python3 -m http.server -d www 8000             # then open http://127.0.0.1:8000/
```

The output files are git-ignored. The script needs only the Python standard library.

## The story

Two years ending on `--end` (default: today):

| When | What happens | Shows up in |
|---|---|---|
| First 5 months | Routine check-ups and labs, all normal | Timeline, Trends |
| Month 5 | Fatigue and low hemoglobin, CT, biopsy, diagnosis, PET/CT, port placed | Timeline, Lab heatmap |
| Months 5–10 | Six 21-day treatment cycles; blood counts dip mid-cycle, liver tests rise in some cycles | Trends, Lab heatmap |
| Cycle 3 | Five-day hospital stay for fever with low counts, daily inpatient labs | Timeline, Costs |
| After treatment | Clear PET/CT, port removed, 8 weeks of physical therapy, follow-ups every 3 months | Timeline, Health |
| Throughout | Claims with a coinsurance cap and one denied claim, premiums and bill payments | Costs |
| From a month before diagnosis | Watch (steps, heart rate, HRV, sleep stages, workouts) and smart scale (weight, body fat) | Health |
