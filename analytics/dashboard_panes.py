"""Panel building blocks shared by the lab dashboards."""

import panel as pn
import plotly.graph_objects as go


def plot_pane() -> pn.pane.Plotly:
    """Plotly pane whose height follows the figure's own layout height. Without a
    pane height the browser sizes the plot from its contents: the first time the
    figure changed, the plot collapsed to 0 px and the table below moved up over
    its place, staying there until the next change."""
    pane = pn.pane.Plotly(go.Figure(), config={"responsive": True}, sizing_mode="stretch_width")

    def _sync_height(event):
        if event.new is not None and event.new.layout.height:
            pane.height = event.new.layout.height

    pane.param.watch(_sync_height, "object")
    return pane
