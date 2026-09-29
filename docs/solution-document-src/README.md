# Solution Document generator

`FleetPulse_Solution_Document.docx` (in `docs/`) follows the hackathon template's sections 1-17.
Every number is read from evidence files by `make_results.py`:
- `data/models/m1/report.json`
- `data/logs/load_api.json`
- EXPLAIN results and other measured outputs

To regenerate:
```bash
mkdir -p tpl/word/media && unzip -j -o <template.docx> 'word/media/*' -d tpl/word/media   # header logos
npm install docx@9
python3 make_results.py "<rule alert latency summary>" "<snapshot latency summary>"
node build.js ../FleetPulse_Solution_Document.docx
```
Team name, members and the video link are placeholders to fill in Word before exporting to PDF.
