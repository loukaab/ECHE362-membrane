"""The project's current OOON design basis, fully connected and ready to run."""

from .flowsheet import Flowsheet
from .models import Connection, Equipment, FeedProperties, CompressorProperties
from .presets import PresetStore


def default_flowsheet(presets: PresetStore) -> Flowsheet:
    sheet = Flowsheet("OOON design basis")
    sheet.add(Equipment("F-1", "Air feed", "feed", FeedProperties(), 70, 300))
    sheet.add(Equipment("C-1", "C-1", "compressor", CompressorProperties(4500, "flow", 17500), 280, 300))
    previous = "C-1"
    for i, label in enumerate("OOON", 1):
        node_id = f"M-{i}"
        sheet.add(Equipment(node_id, node_id, "membrane", presets.get(label), 280 + i * 220, 300))
        source_port = "outlet" if i == 1 else "retentate"
        sheet.connect(Connection(f"S-{i+1}", f"S-{i+1}", previous, source_port, node_id, "inlet"))
        sheet.connect(Connection(f"P-{i}", "O2 product" if i == 1 else f"Purge {i}",
                                 node_id, "permeate", outlet="oxygen" if i == 1 else "vent",
                                 endpoint=(365 + i * 220, 600)))
        previous = node_id
    sheet.connect(Connection("S-1", "Fresh air", "F-1", "outlet", "C-1", "inlet"))
    sheet.connect(Connection("R-4", "N2 product", "M-4", "retentate", outlet="nitrogen",
                             endpoint=(1510, 343)))
    sheet.reset_legend_position()
    return sheet
