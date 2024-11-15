import param
import panel as pn
import numpy as np

from panel.custom import JSComponent

class OrientationPane(JSComponent):
    _bundle = 'OrientationPane.bundle.js'
    _esm = 'orientationpane.js'

    orientation = param.List(default = [0.0, 0.0, 0.0, 1.0])

    _importmap = {
        "imports": {
            "three": "https://esm.sh/three@0.170.0",
        }
    }