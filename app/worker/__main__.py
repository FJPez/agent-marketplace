# `python -m app.worker` runs this file as `__main__` and imports `app.worker` under its
# own name, so the worker's logger is "app.worker" and is configured with the `app` tree.
from app.worker import main

main()
