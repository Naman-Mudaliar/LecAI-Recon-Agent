# renders the conflicts from one real reconciliation cycle into a pdf -
# meant to be handed to someone who wants the recon logic and the
# underlying eta math on paper, not scrolling back through a terminal.
# only conflicts (+ suspects + possible cancellations) go in here, not the
# whole cycle - the terminal report already shows everything, this is the
# "here's exactly why, in writing" leave-behind for the ones that matter.
#
# reuses agent.policy.recon_steps and agent.time_utils.format_eta - the
# exact same functions the terminal report calls, so the pdf can never
# show a different "why" than what you saw on screen.
#
# fpdf2 note: multi_cell() no longer resets the cursor back to the left
# margin by default (that changed a while back) - every call below is
# explicit about new_x/new_y for that reason, learned the hard way after
# it threw "not enough horizontal space" from the cursor drifting to the
# right margin and never coming back.

from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from agent import policy, time_utils

_VERDICT_LABEL = {
    "LIVE_WINS": "LIVE WINS - live feed is the resolved value",
    "ANOMALY": "ANOMALY - flagged, no value picked",
    "DATA_QUALITY_FLAG": "DATA QUALITY FLAG - flagged, no value picked",
    "WITHHELD_UNDER_REVIEW": "FROZEN - MANUAL REVIEW REQUIRED",
}

_CONFLICT_VERDICTS = {"LIVE_WINS", "ANOMALY", "DATA_QUALITY_FLAG", "WITHHELD_UNDER_REVIEW"}

_NEXT_LINE = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}


class _Report(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Route 263 Reconciliation - Conflict Report", **_NEXT_LINE)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, self._subtitle, **_NEXT_LINE)
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def _heading(pdf, text):
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(210, 210, 210)
    pdf.cell(0, 7, text, fill=True, **_NEXT_LINE)
    pdf.ln(1)


def _line(pdf, text, style=""):
    pdf.set_font("Helvetica", style, 9)
    pdf.multi_cell(0, 5, text, **_NEXT_LINE)


def _field_row(pdf, label, value):
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(28, 5, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, str(value), **_NEXT_LINE)


def _print_conflict_field(pdf, r):
    pdf.ln(1)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(235, 235, 235)
    pdf.cell(0, 6, f"  field: {r['field']}", fill=True, **_NEXT_LINE)

    _field_row(pdf, "live feed:", r["live_value"])
    _field_row(pdf, "warehouse:", r["warehouse_value"])
    _field_row(pdf, "verdict:", _VERDICT_LABEL.get(r["verdict"], r["verdict"]))
    if r["resolved_value"] is not None:
        _field_row(pdf, "resolved:", r["resolved_value"])
    _field_row(pdf, "source:", r.get("source") or "confirmed")
    pdf.ln(1)
    _line(pdf, f"recon logic: {r['reason']}", style="I")
    _line(pdf, f"to clear: {policy.recon_steps(r)}")
    pdf.ln(2)


def _print_bus(pdf, vr):
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, f"bus {vr['vehicle_id']}  ->  {vr['trip_headsign']} (final destination)", **_NEXT_LINE)
    _line(pdf, f"position: {vr['lat']:.4f}, {vr['lon']:.4f}")

    if vr["next_stop_name"]:
        _line(pdf, f"next stop: {vr['next_stop_name']} (scheduled {vr['next_stop_scheduled']})")
        # the underlying eta calc itself - straight-line speed projection
        # from the last two real gps fixes, see agent/eta.py. shown
        # verbatim, not summarised, since this is exactly what the user
        # asked to see on paper
        _line(pdf, f"eta calc: {vr['next_stop_eta_detail']}", style="I")
    else:
        _line(pdf, "next stop: none left on this trip")
    pdf.ln(1)


def has_anything_to_report(summary):
    # dont bother writing a pdf for a cycle where nothing happened -
    # matches the "only conflicts go in" framing, an empty pdf isnt useful
    # to anyone
    suspects = [vr for vr in summary["vehicles"] if vr["outcome"]["suspect"]]
    conflicts = [
        r for vr in summary["vehicles"] if not vr["outcome"]["suspect"]
        for r in vr["outcome"]["resolutions"] if r["verdict"] in _CONFLICT_VERDICTS
    ]
    return bool(suspects or conflicts or summary["cancellations"])


def generate(summary, path):
    now_dt = datetime.fromisoformat(summary["timestamp"])
    ts = time_utils.format_london(now_dt)

    suspects = [vr for vr in summary["vehicles"] if vr["outcome"]["suspect"]]
    normal_with_conflicts = []
    for vr in summary["vehicles"]:
        if vr["outcome"]["suspect"]:
            continue
        conflicts = [r for r in vr["outcome"]["resolutions"] if r["verdict"] in _CONFLICT_VERDICTS]
        if conflicts:
            normal_with_conflicts.append((vr, conflicts))

    total_conflicts = sum(len(c) for _, c in normal_with_conflicts)

    pdf = _Report()
    pdf._subtitle = (
        f"Cycle: {ts}   |   {len(suspects)} suspect   {total_conflicts} field conflict(s)   "
        f"{len(summary['cancellations'])} possible cancellation(s)"
    )
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    if suspects:
        _heading(pdf, "SUSPECT (quarantined - not resolved field-by-field this cycle)")
        for vr in suspects:
            _print_bus(pdf, vr)
            _line(pdf, f"why: {vr['outcome']['reason']}")
            _line(pdf, "to clear: quarantined this cycle only - resolves field-by-field again "
                       "once 3 or fewer of 6 fields disagree in a future cycle")
            pdf.ln(3)

    if normal_with_conflicts:
        _heading(pdf, "FIELD CONFLICTS")
        for vr, conflicts in normal_with_conflicts:
            _print_bus(pdf, vr)
            for r in conflicts:
                _print_conflict_field(pdf, r)
            pdf.ln(2)

    if summary["cancellations"]:
        _heading(pdf, "POSSIBLE CANCELLATIONS (scheduled, never seen live, past grace period)")
        for c in summary["cancellations"]:
            _line(pdf, f"{c['trip_id'][:24]}...   {c['record']['reason']}")
            _line(pdf, f"to clear: {policy.recon_steps(c['record'])}")
            pdf.ln(1)

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path
