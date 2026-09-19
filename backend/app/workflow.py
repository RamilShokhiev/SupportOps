from contextlib import contextmanager
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .integrations import diagnostics


class State(TypedDict, total=False):
    ticket_id: str
    organization_id: str
    text: str
    language: str
    diagnostic_mode: str
    documents: list[dict]
    diagnostics: list[dict]
    analysis: dict
    decision: dict


@contextmanager
def persistent_graph(settings):
    if settings.database_url.startswith('postgresql'):
        from langgraph.checkpoint.postgres import PostgresSaver
        context = PostgresSaver.from_conn_string(settings.database_url.replace('postgresql+psycopg://', 'postgresql://'))
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver
        context = SqliteSaver.from_conn_string(settings.checkpoint_sqlite_path)
    with context as checkpointer:
        checkpointer.setup()
        yield build_graph(settings, checkpointer)


def build_graph(settings, checkpointer):
    from .intelligence import analyze

    def collect(state):
        return {'diagnostics': diagnostics(state['text'], settings, state.get('diagnostic_mode', 'normal'))}

    def analyze_ticket(state):
        return {'analysis': analyze(state['text'], state['language'], state['documents'], state['diagnostics'], settings)}

    def human_review(state):
        decision = interrupt({'ticket_id': state['ticket_id'], 'next_step': state['analysis']['next_step'], 'message': 'Human review required. This graph never executes external writes.'})
        return {'decision': decision}

    builder = StateGraph(State)
    builder.add_node('diagnostics', collect)
    builder.add_node('analysis', analyze_ticket)
    builder.add_node('human_review', human_review)
    builder.add_edge(START, 'diagnostics')
    builder.add_edge('diagnostics', 'analysis')
    builder.add_edge('analysis', 'human_review')
    builder.add_edge('human_review', END)
    return builder.compile(checkpointer=checkpointer)
