#!/bin/zsh
cd "$(dirname "$0")"
clear
echo "Starting Gridlock Sprint…"
# The project virtualenv carries Pillow and ReportLab, which answer-sheet
# scanning and packet building need. Fall back to the system Python for
# move-by-move grading only.
if [[ -x ".venv/bin/python" ]]; then
  .venv/bin/python server.py
else
  echo "No .venv found — answer-sheet scanning will be unavailable."
  echo "To enable it: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  echo
  python3 server.py
fi
exit_code=$?
echo
if [[ $exit_code -ne 0 ]]; then
  echo "The grading station could not start. The error is shown above."
fi
echo "Press any key to close this window."
read -k 1
