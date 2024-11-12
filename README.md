# ezmsg-unicorn
g.tec Unicorn Hybrid Black integration for ezmsg

## License
Copyright 2023 JHUAPL; currently not released under an open source license.

## Install
```uv sync --all-extras```

## Run Dashboard
```uv run dashboard```
```
usage: dashboard [-h] [--port PORT] [--address ADDRESS] [--n_samp N_SAMP]

Unicorn Dashboard

options:
  -h, --help            show this help message and exit

dashboard:
  --port PORT           port to host dashboard on. [0 = any open port, default]

device:
  --address ADDRESS, -a ADDRESS
                        bluetooth address of Unicorn to autoconnect to (XX:XX:XX:XX:XX:XX)
  --n_samp N_SAMP       number of data frames per message
```