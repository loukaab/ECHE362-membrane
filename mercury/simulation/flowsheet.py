"""Acyclic material-flow graph, validation, execution and input-only persistence."""

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .models import (
    Connection, Equipment, EquipmentResult, Stream, SimulationResult, FeedProperties,
    MembraneProperties, CompressorProperties, SplitterProperties, ProductProperties,
    PROPERTY_TYPES, Properties, evaluate_product, validate_properties,
    TextAnnotation, LegendPosition, LEGEND_ID,
)
from .numerics import CompressorMap, checked_membrane, ideal_compressor_power
from .persistence import read_json, write_json
from .units import RESIDUAL_TOLERANCE, close


@dataclass(frozen=True)
class ValidationIssue:
    message: str
    node_id: str | None = None
    connection_id: str | None = None


class SimulationError(ValueError):
    def __init__(self, message: str, node_id: str | None = None,
                 issues: list[ValidationIssue] | None = None):
        super().__init__(message)
        self.node_id = node_id
        self.issues = issues or [ValidationIssue(message, node_id)]


class Flowsheet:
    def __init__(self, name: str = "Untitled flowsheet"):
        self.name = name
        self.nodes: dict[str, Equipment] = {}
        self.connections: dict[str, Connection] = {}
        self.annotations: dict[str, TextAnnotation] = {}
        self.legend = LegendPosition()

    def object_ids(self) -> set[str]:
        return set(self.nodes) | set(self.connections) | set(self.annotations) | {LEGEND_ID}

    def add_annotation(self, annotation: TextAnnotation) -> None:
        if annotation.id in self.object_ids():
            raise ValueError(f"Object ID {annotation.id} already exists")
        self.annotations[annotation.id] = annotation

    def reset_legend_position(self) -> None:
        if not self.nodes:
            self.legend = LegendPosition()
            return
        right = max(n.x+n.width for n in self.nodes.values())
        right = max([right] + [c.endpoint[0] for c in self.connections.values() if c.endpoint])
        # Above/right keeps the legend clear even at the minimum zoom, where
        # text has a minimum readable screen size instead of shrinking forever.
        self.legend = LegendPosition(right+80, min(n.y for n in self.nodes.values())-400)

    def add(self, equipment: Equipment) -> None:
        validate_properties(equipment)
        if equipment.id in self.object_ids():
            raise ValueError(f"Equipment ID {equipment.id} already exists")
        self.nodes[equipment.id] = deepcopy(equipment)

    def connect(self, connection: Connection) -> None:
        if connection.id in self.object_ids():
            raise ValueError("Connection ID already exists")
        issue = self._connection_issue(connection)
        if issue:
            raise ValueError(issue.message)
        self.connections[connection.id] = connection

    def _connection_issue(self, c: Connection) -> ValidationIssue | None:
        if not isinstance(c.id, str) or not c.id.strip() or not isinstance(c.name, str) or not c.name.strip():
            return ValidationIssue("Stream ID and name must be nonempty", connection_id=c.id)
        source, target = self.nodes.get(c.source_node), self.nodes.get(c.target_node)
        if source is None or (not c.is_terminal and target is None):
            return ValidationIssue(f"{c.name}: connection refers to missing equipment", connection_id=c.id)
        if c.is_terminal:
            if c.outlet not in ("oxygen", "nitrogen", "report", "vent"):
                return ValidationIssue(f"{c.name}: select a product or purge/vent outlet", connection_id=c.id)
        elif c.outlet is not None or c.endpoint is not None:
            return ValidationIssue(f"{c.name}: an equipment connection cannot also be a terminal outlet", connection_id=c.id)
        if source.kind not in PROPERTY_TYPES or (target and target.kind not in PROPERTY_TYPES):
            return ValidationIssue(f"{c.name}: unknown equipment type", connection_id=c.id)
        if c.source_port not in source.outlet_ports or (target and c.target_port not in target.inlet_ports):
            return ValidationIssue(f"{c.name}: connect an output port to an input port", source.id, c.id)
        if target and source.id == target.id:
            return ValidationIssue("Recycle flowsheets are not yet supported", source.id, c.id)
        for existing in self.connections.values():
            if existing.id == c.id:
                continue
            if (existing.source_node, existing.source_port) == (c.source_node, c.source_port):
                return ValidationIssue(f"{source.name} {c.source_port} is already connected; use a splitter",
                                       source.id, c.id)
            if target and (existing.target_node, existing.target_port) == (c.target_node, c.target_port):
                return ValidationIssue(f"{target.name} {c.target_port} is already connected; use a mixer",
                                       target.id, c.id)
        return None

    def delete(self, object_id: str) -> None:
        if self.annotations.pop(object_id, None) is not None:
            return
        self.connections.pop(object_id, None)
        if self.nodes.pop(object_id, None) is not None:
            self.connections = {key: c for key, c in self.connections.items()
                                if object_id not in (c.source_node, c.target_node)}

    def direct_feed(self, compressor_id: str) -> Equipment | None:
        for c in self.connections.values():
            if c.target_node == compressor_id and c.target_port == "inlet":
                source = self.nodes.get(c.source_node)
                if source and source.kind == "feed":
                    return source
        return None

    def update_properties(self, node_id: str, properties: Properties, compressor_map: CompressorMap,
                          name: str | None = None) -> None:
        """Transactional edits; linked feed/compressor values change together."""
        draft = deepcopy(self)
        node = draft.nodes[node_id]
        old = node.properties
        node.properties = properties
        if name is not None:
            node.name = name.strip()
        validate_properties(node)
        if node.kind == "compressor":
            draft._link_compressor(node, compressor_map)
        elif node.kind == "feed":
            for c in draft.connections.values():
                if c.source_node == node.id and c.target_node in draft.nodes and draft.nodes[c.target_node].kind == "compressor":
                    comp = draft.nodes[c.target_node]
                    p = comp.properties
                    if properties.flow_slpm != old.flow_slpm:
                        comp.properties = replace(p, mode="flow", flow_slpm=properties.flow_slpm,
                                                  outlet_pressure_psia=None)
                    draft._link_compressor(comp, compressor_map)
        self.nodes = draft.nodes

    def _link_compressor(self, node: Equipment, compressor_map: CompressorMap) -> None:
        p = node.properties
        compressor_map.limits(p.rpm)
        feed = self.direct_feed(node.id)
        if feed is None:
            if p.mode == "flow":
                # Flow bounds are independent of the yet-to-be-calculated inlet pressure.
                compressor_map.forward(p.flow_slpm, p.rpm, 14.7)
            return
        f = feed.properties
        if p.mode == "pressure":
            flow = compressor_map.inverse(p.outlet_pressure_psia, p.rpm, f.pressure_psia)
            feed.properties = replace(f, flow_slpm=flow)
        elif p.mode == "flow":
            compressor_map.forward(p.flow_slpm, p.rpm, f.pressure_psia)
            feed.properties = replace(f, flow_slpm=p.flow_slpm)
        else:
            compressor_map.forward(f.flow_slpm, p.rpm, f.pressure_psia)

    def topological_order(self) -> list[str]:
        indegree = {key: 0 for key in self.nodes}
        adjacency = {key: [] for key in self.nodes}
        for c in self.connections.values():
            if c.is_terminal:
                if c.source_node not in self.nodes:
                    raise SimulationError("Connection refers to missing equipment")
                continue
            if c.source_node not in self.nodes or c.target_node not in self.nodes:
                raise SimulationError("Connection refers to missing equipment")
            indegree[c.target_node] += 1
            adjacency[c.source_node].append(c.target_node)
        ready = deque(sorted(key for key, count in indegree.items() if count == 0))
        order = []
        while ready:
            key = ready.popleft()
            order.append(key)
            for target in sorted(adjacency[key]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if len(order) != len(self.nodes):
            affected = [key for key, count in indegree.items() if count]
            raise SimulationError("Recycle flowsheets are not yet supported", issues=[
                ValidationIssue("Recycle flowsheets are not yet supported", key) for key in affected])
        return order

    def validate(self, compressor_map: CompressorMap | None = None) -> list[ValidationIssue]:
        issues = []
        if not any(node.kind == "feed" for node in self.nodes.values()):
            issues.append(ValidationIssue("At least one feed stream is required"))
        for node in self.nodes.values():
            try:
                validate_properties(node)
            except (ValueError, TypeError) as error:
                issues.append(ValidationIssue(f"{node.name}: {error}", node.id))
        connection_issues = [issue for c in self.connections.values() if (issue := self._connection_issue(c))]
        issues.extend(connection_issues)
        if issues:
            # Continue reporting missing ports for recognized node types, but avoid
            # evaluating invalid properties or broken graph references.
            bad_nodes = {issue.node_id for issue in issues}
        else:
            bad_nodes = set()
        for node in self.nodes.values():
            if node.kind not in PROPERTY_TYPES:
                continue
            for port in node.inlet_ports:
                if not any(c.target_node == node.id and c.target_port == port for c in self.connections.values()):
                    issues.append(ValidationIssue(f"{node.name} {port} has no inlet stream", node.id))
            for port in node.outlet_ports:
                if not any(c.source_node == node.id and c.source_port == port for c in self.connections.values()):
                    issues.append(ValidationIssue(f"{node.name} {port} outlet is not connected", node.id))
            if compressor_map and node.kind == "compressor" and node.id not in bad_nodes:
                try:
                    p = node.properties
                    compressor_map.limits(p.rpm)
                    feed = self.direct_feed(node.id)
                    if feed and feed.id not in bad_nodes:
                        f = feed.properties
                        actual = compressor_map.forward(f.flow_slpm, p.rpm, f.pressure_psia)
                        self._check_compressor_target(p, f.flow_slpm, actual["p_out"])
                    elif p.mode == "flow":
                        compressor_map.forward(p.flow_slpm, p.rpm, 14.7)
                except (ValueError, TypeError) as error:
                    issues.append(ValidationIssue(f"{node.name}: {error}", node.id))
        if not connection_issues:
            try:
                self.topological_order()
            except SimulationError as error:
                issues.extend(error.issues)
            reachable = {node.id for node in self.nodes.values() if node.kind == "feed"}
            while True:
                expanded = reachable | {c.target_node for c in self.connections.values()
                                         if not c.is_terminal and c.source_node in reachable}
                if expanded == reachable:
                    break
                reachable = expanded
            issues.extend(ValidationIssue(f"{node.name} is not reachable from a feed", node.id)
                          for node in self.nodes.values() if node.id not in reachable)
        return issues

    @staticmethod
    def _check_compressor_target(p: CompressorProperties, flow: float, pressure: float) -> None:
        if p.mode == "flow" and not close(flow, p.flow_slpm):
            raise ValueError(f"Incoming flow is {flow:.3f} slpm, but the requested flow is {p.flow_slpm:.3f}; "
                             "adjust the upstream feed/split or choose 'inlet' control")
        if p.mode == "pressure" and not close(pressure, p.outlet_pressure_psia):
            raise ValueError(f"Incoming flow gives {pressure:.3f} psia, not the requested "
                             f"{p.outlet_pressure_psia:.3f} psia; adjust upstream flow or choose 'inlet' control")

    def simulate(self, compressor_map: CompressorMap) -> SimulationResult:
        issues = self.validate(compressor_map)
        if issues:
            raise SimulationError("Cannot run simulation:\n" + "\n".join(i.message for i in issues), issues=issues)
        result = SimulationResult()
        order = self.topological_order()
        port_streams: dict[tuple[str, str], Stream] = {}
        feeds = []
        for key in order:
            node = self.nodes[key]
            p = node.properties
            inlets = {}
            for c in self.connections.values():
                if c.target_node == key:
                    stream = replace(port_streams[(c.source_node, c.source_port)], name=c.name)
                    inlets[c.target_port] = stream
                    result.streams[c.id] = stream
            equipment_result = EquipmentResult(inlets=inlets)
            outlets = {}
            try:
                if node.kind == "feed":
                    outlets["outlet"] = Stream.binary(node.name, p.flow_slpm, p.oxygen, p.pressure_psia)
                    feeds.append(outlets["outlet"])
                elif node.kind == "membrane":
                    feed = inlets["inlet"]
                    calculated = checked_membrane(feed, p)
                    for port in ("retentate", "permeate"):
                        outlets[port] = Stream.binary(node.name + " " + port, calculated[port + "_flow"],
                                                       calculated[port + "_O2"], feed.pressure_psia if port == "retentate"
                                                       else p.permeate_pressure_psia)
                    equipment_result.stage_cut = calculated["stage_cut"]
                    if not 0.20 <= calculated["stage_cut"] <= 0.40:
                        result.warnings.append(f"{node.name}: stage cut {calculated['stage_cut']:.4f} "
                                               "is outside the recommended 0.20–0.40 range")
                elif node.kind == "compressor":
                    feed = inlets["inlet"]
                    calculated = compressor_map.forward(feed.flow_slpm, p.rpm, feed.pressure_psia)
                    if p.mode == "pressure":
                        compressor_map.inverse(p.outlet_pressure_psia, p.rpm, feed.pressure_psia)
                    self._check_compressor_target(p, feed.flow_slpm, calculated["p_out"])
                    outlets["outlet"] = replace(feed, name=node.name + " outlet", pressure_psia=calculated["p_out"])
                    equipment_result.ideal_power_kw = ideal_compressor_power(feed.flow_slpm, calculated["p_ratio"])
                elif node.kind == "splitter":
                    feed = inlets["inlet"]
                    outlets = {"a": replace(feed, name=node.name + " a", flow_slpm=feed.flow_slpm * p.fraction),
                               "b": replace(feed, name=node.name + " b", flow_slpm=feed.flow_slpm * (1 - p.fraction))}
                elif node.kind == "mixer":
                    a, b = inlets["a"], inlets["b"]
                    if not close(a.pressure_psia, b.pressure_psia):
                        raise ValueError(f"Mixer inlet pressures differ ({a.pressure_psia:.4f} vs {b.pressure_psia:.4f} psia); "
                                         "pressure equalization is not modeled")
                    flow = a.flow_slpm + b.flow_slpm
                    if flow <= 0:
                        raise ValueError("Mixer requires positive combined flow")
                    oxygen = (a.flow_slpm * a.oxygen + b.flow_slpm * b.oxygen) / flow
                    outlets["outlet"] = Stream.binary(node.name + " outlet", flow, oxygen, a.pressure_psia)
                elif node.kind == "product":
                    result.products[key] = evaluate_product(node.name, p.role, inlets["inlet"])
                elif node.kind == "vent":
                    result.vents[key] = inlets["inlet"]
            except (ValueError, RuntimeError, ArithmeticError) as error:
                conditions = "; ".join(f"{port}: {s.flow_slpm:.4g} slpm, O2={s.oxygen:.6g}, "
                                       f"{s.pressure_psia:.4g} psia" for port, s in inlets.items())
                raise SimulationError(f"{node.name} failed: {error}" + (f"\nFeed conditions: {conditions}" if conditions else ""),
                                      node.id) from error
            equipment_result.outlets = outlets
            result.equipment[key] = equipment_result
            port_streams.update({(key, port): stream for port, stream in outlets.items()})
        result.execution_order = order
        for c in self.connections.values():
            if c.is_terminal:
                stream = replace(port_streams[(c.source_node, c.source_port)], name=c.name)
                result.streams[c.id] = stream
                if c.outlet == "vent":
                    result.vents[c.id] = stream
                else:
                    result.products[c.id] = evaluate_product(c.name, c.outlet, stream)
        terminal = [p.stream for p in result.products.values()] + list(result.vents.values())
        feed_flow = sum(s.flow_slpm for s in feeds)
        result.flow_balance_error_slpm = sum(s.flow_slpm for s in terminal) - feed_flow
        result.oxygen_balance_error_slpm = sum(s.flow_slpm * s.oxygen for s in terminal) - sum(s.flow_slpm * s.oxygen for s in feeds)
        if max(abs(result.flow_balance_error_slpm), abs(result.oxygen_balance_error_slpm)) / max(feed_flow, 1) > RESIDUAL_TOLERANCE:
            raise SimulationError("Flowsheet external flow or O2 balance exceeds numerical tolerance")
        return result

    def to_dict(self) -> dict:
        return {"version": 3, "name": self.name,
                "equipment": [asdict(node) for node in self.nodes.values()],
                "connections": [asdict(c) for c in self.connections.values()],
                "annotations": [asdict(a) for a in self.annotations.values()],
                "legend": asdict(self.legend)}

    @classmethod
    def from_dict(cls, document: dict) -> "Flowsheet":
        try:
            if type(document.get("version")) is not int or document["version"] not in (1, 2, 3) or not isinstance(document.get("name"), str):
                raise ValueError("Unsupported flowsheet document; expected version 1, 2 or 3 and a name")
            if not isinstance(document.get("equipment"), list) or not isinstance(document.get("connections"), list):
                raise ValueError("Equipment and connections must be lists")
            sheet = cls(document["name"])
            for value in document["equipment"]:
                fields = dict(value)
                kind = fields["kind"]
                if kind not in PROPERTY_TYPES:
                    raise ValueError(f"Unknown equipment type {kind!r}")
                fields["properties"] = PROPERTY_TYPES[kind](**fields["properties"])
                sheet.add(Equipment(**fields))
            for value in document["connections"]:
                sheet.connect(Connection(**value))
            if document["version"] == 3:
                if not isinstance(document.get("annotations"), list) or not isinstance(document.get("legend"), dict):
                    raise ValueError("Version 3 requires an annotations list and legend position")
                for value in document["annotations"]:
                    sheet.add_annotation(TextAnnotation(**value))
                sheet.legend = LegendPosition(**document["legend"])
            sheet.migrate_terminal_nodes()
            if document["version"] < 3:
                sheet.reset_legend_position()
            # Structurally valid drafts may be incomplete or cyclic; Validate/Run
            # reports those issues, so unfinished designs can still be saved.
            return sheet
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"Malformed flowsheet: {error}") from error

    def migrate_terminal_nodes(self) -> None:
        """Convert connected legacy sink boxes to stream-owned terminal outlets.

        Unconnected legacy placeholders are retained so unfinished v1 designs
        lose no inputs; the editor draws these as arrow markers, never boxes.
        """
        for node in list(self.nodes.values()):
            if node.kind not in ("product", "vent"):
                continue
            incoming = [c for c in self.connections.values() if c.target_node == node.id]
            if len(incoming) == 1:
                c = incoming[0]
                self.connections[c.id] = replace(
                    c, name=node.name, target_node=None, target_port=None,
                    outlet=node.properties.role if node.kind == "product" else "vent",
                    endpoint=(node.x + node.width / 2, node.y + node.height / 2))
                del self.nodes[node.id]

    def save(self, path: Path) -> None:
        document = self.to_dict()
        self.from_dict(document)
        write_json(path, document)

    @classmethod
    def load(cls, path: Path) -> "Flowsheet":
        return cls.from_dict(read_json(path))
