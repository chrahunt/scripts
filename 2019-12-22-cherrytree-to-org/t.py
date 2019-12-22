"""
Map hierarchical notes from CherryTree to org-friendly plain text format.

Each node has:
- text
- created time
- modified time
- children
- name

These are mapped to org nodes.

There are two kinds of nodes:

1. ones with text
2. ones without text
3. ones without text and with children

for each:

1. created time
2. modified time

we want to create the outline level at the created time and fill it with text
at the modified time.

Issues:
1. Occasionally git receives SIGBUS and crashes
"""
import logging
import re
import shutil
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from itertools import chain, count, islice
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

import click
import click_log
import git
from lxml import etree


logger = logging.getLogger(__name__)
click_log.basic_config(logger)


def read_xml_file(path: str) -> Any:
    with open(path, "rb") as f:
        # recover=True prevents non-printing characters (which may exist due
        # to some pasted text) from interrupting parsing
        return etree.parse(f, etree.XMLParser(recover=True))


@dataclass
class Node:
    name: str
    header: str
    text: str
    created: datetime
    modified: datetime


def parse_nodes(node: etree.Element) -> Iterator[Node]:
    """Convert a CherryTree XML tree into a sequence of nodes.
    """
    asterisk_re = re.compile(r"^\*", re.M)

    def parse_date(timestamp: str) -> Optional[datetime]:
        if not isinstance(timestamp, (int, float)):
            timestamp = float(timestamp)
        if not timestamp:
            return None
        return datetime.fromtimestamp(timestamp)

    def render_node(
        node: etree.Element, level: int, default_created: datetime, default_modified: datetime
    ) -> Iterator[Node]:
        name = node.get("name")
        header = f"{'*' * level} {name}\n"
        logger.debug("%sHandling %s", " " * level, name)

        created = parse_date(node.get('ts_creation')) or default_created
        modified = parse_date(node.get('ts_lastsave')) or default_modified

        text = []
        for child in node.iterchildren("rich_text"):
            if not child.text:
                continue
            # Escape leading asterisks
            text.append(asterisk_re.sub(r"\\ast{}", child.text).rstrip())
            text.append("\n")

        text = "".join(text)

        yield Node(name, header, text, created, modified)

        for child in node.iterchildren("node"):
            yield from render_node(child, level + 1, created, modified)

    def render_nodes(node: etree.Element) -> Iterable[str]:
        defaults = datetime.now()
        for child in node:
            yield from render_node(child, 1, defaults, defaults)

    return render_nodes(node)


@dataclass
class Entry:
    time: datetime
    node: Node
    description: str
    text: Iterator[str]


def get_documents_by_time(node: etree.Element) -> Iterator[Entry]:
    nodes = list(parse_nodes(node))

    # Times will correspond to commits.
    times = sorted(
        chain.from_iterable(
            ((n.created, i), (n.modified, i)) for i, n in enumerate(nodes)
        )
    )
    nodes_by_index = dict(enumerate(nodes))

    def document(seen: Dict[int, int]) -> Iterator[str]:
        # Nodes are in document order.
        for node_id, times_seen in sorted(seen.items()):
            assert times_seen
            yield nodes_by_index[node_id].header
            if times_seen == 2:
                text = nodes_by_index[node_id].text
                if text:
                    yield text

    def no_consecutive_duplicates(times):
        last_t, last_id = None, None
        times_iter = iter(times)
        last_t, last_id = next(times_iter)
        seen = Counter()
        seen[last_id] += 1
        for t, node_id in times:
            seen[node_id] += 1
            if node_id != last_id:
                yield last_t, last_id, seen
            last_t, last_id = t, node_id
        yield last_t, last_id, seen

    for t, node_id, counts in no_consecutive_duplicates(times):
        #logger.debug("Providing entry %d of %d", i + 1, len(times))
        yield Entry(
            t,
            nodes_by_index[node_id],
            "" if counts[node_id] == 2 else "section: ",
            document(counts),
        )


def commit_documents_by_time(entries: Iterator[Entry], output_dir: Path, repo):
    document_path = output_dir / document_name
    for entry in entries:
        document_path.write_text("".join(entry.text), encoding="utf-8")
        repo.index.add([document_name])

        message = f"{entry.description}{entry.node.name}"
        repo.index.commit(
            message, author_date=entry.time.strftime("%Y-%m-%d %H:%M:%S -0500")
        )


document_name = "notes.org"


template_repo = {
    ".gitattributes": "* text=auto\n",
    ".gitignore": ".#*\n",
    document_name: "",
}


@click.command()
@click.option(
    "--strategy", type=click.Choice(["onefile"]), default="onefile"
)
@click_log.simple_verbosity_option(logger)
@click.argument("cherrytree_db", type=click.Path())
@click.argument("output_dir", type=click.Path())
def convert(strategy, cherrytree_db, output_dir):
    et = read_xml_file(cherrytree_db)

    # Prepare output directory
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True)

    for name, value in template_repo.items():
        path = output_dir.joinpath(name)
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(value, encoding="utf-8")

    repo = git.Repo.init(output_dir)
    repo.index.add(template_repo.keys())
    repo.index.commit("init")

    start = time.time()
    if strategy == "onefile":
        entries = get_documents_by_time(et.getroot())
        commit_documents_by_time(entries, output_dir, repo)
    else:
        raise ValueError(f"Unknown strategy {strategy}")
    duration = time.time() - start
    logger.debug("duration: %f", duration)


if __name__ == "__main__":
    convert()
