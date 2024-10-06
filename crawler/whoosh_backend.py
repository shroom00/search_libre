from itertools import chain
import os
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, Generator, Literal, Optional, Tuple, overload, Union
from typing_extensions import override
from whoosh.query import Phrase, Query, Every
from whoosh.fields import SchemaClass, TEXT, ID, DATETIME, NUMERIC
from whoosh.highlight import (
    FIRST,
    Formatter,
    Highlighter,
    PinpointFragmenter,
    get_text,
    set_matched_filter_phrases as whoosh_set_matched_filter_phrases,
)

from whoosh.index import FileIndex
from whoosh.multiproc import MpWriter
from whoosh.qparser import FieldsPlugin, OrGroup, QueryParser
from whoosh.query.qcore import _NullQuery
from whoosh.searching import Searcher
from whoosh.support.charset import accent_map
from whoosh.writing import SegmentWriter
from whoosh.analysis import (
    CharsetFilter,
    Filter,
    RegexTokenizer,
    LowercaseFilter,
    MultiFilter,
    SubstitutionFilter,
    IntraWordFilter,
    Token,
)


def SimpleParser(fieldname, schema, plugins=[], **kwargs):
    """Returns a QueryParser configured to support +, -, and (custom) phrase
    syntax.
    """
    from whoosh.qparser import plugins as whoosh_plugins, syntax

    # the WhitespacePlugin used to be added here, but it's added to the QueryParser regardless, with `_add_ws_plugin` in `QueryParser.__init__`
    pins = [whoosh_plugins.PlusMinusPlugin(r"(^|\s)\+", r"(^|\s)-"), whoosh_plugins.SingleQuotePlugin] + plugins
    orgroup = kwargs.pop("group", syntax.OrGroup)
    return QueryParser(fieldname, schema, plugins=pins, group=orgroup, **kwargs)


def set_matched_filter_phrases(*args, analyzer=None, analyzer_kwargs={}, **kwargs):
    # we make this so we can pass text as a predefined list without getting errors because we tried to split the list
    # (the default `matched_filter_phrases` function uses split by default to get tokens from text)
    # doing it this way means there's no need to copy the entire original function with only minor tweaks here.
    class ListWrapper(list):
        def __init__(self, baseList):
            super().__init__()
            self.extend(baseList)

        def split(self):
            return self

    if "text" in kwargs:
        text = kwargs["text"]
        text_in_kwargs = True
    else:
        text = args[1]
        text_in_kwargs = False

    if analyzer is not None:
        text = ListWrapper([t.text for t in analyzer(text, **analyzer_kwargs)])
        if text_in_kwargs:
            kwargs["text"] = text
        else:
            args = list(args)
            if "tokens" in kwargs:
                args[0] = text
            else:
                args[1] = text

    return whoosh_set_matched_filter_phrases(*args, **kwargs)


# The default MultiFilter raises an error when there are no tokens, this fixes that
# Currently unused but here for safekeeping
class MultiFilter(MultiFilter):
    def __call__(self, tokens):
        # Only selects on the first token
        t = next(tokens, None)
        if t is not None:
            filter = self.filters.get(t.mode, self.default_filter)
            return filter(chain([t], tokens))
        return []


class DuplicateFilter(Filter):
    def __call__(self, tokens):
        yielded = set()
        for t in tokens:
            t: Token
            token_hash = (
                t.positions,
                t.chars,
                t.stopped,
                t.boost,
                t.removestops,
                t.mode,
                getattr(t, "text", getattr(t, "original", "")),
                getattr(t, "startchar", 0),
                getattr(t, "endchar", 0),
            )
            if token_hash not in yielded:
                yielded.add(token_hash)
                yield t


class AllFilters(Filter):
    def __init__(self, *filters, yield_original: bool = True) -> None:
        assert filters
        super().__init__()
        self.yield_original = yield_original
        self.filters = filters

    def __call__(self, tokens):
        for t in tokens:
            if self.yield_original:
                yield t
            for f in self.filters:
                yield from f([t.copy()])


# This separates on whitespace, while also stripping surrounding punctuation
# e.g. `hello.. world..` becomes `hello world`
WhitespaceTokenizer = RegexTokenizer(
    expression=r"[^\w]*\s+[^\w]*|[^\w]+$|^[^\w]+", gaps=True
)

PunctuationFilter = SubstitutionFilter(r"[^\w]+", r"")

SANITIZATION = DuplicateFilter() | LowercaseFilter() | CharsetFilter(accent_map)
INTRAWORD = IntraWordFilter()

DEFAULT_ANALYZER = (
    WhitespaceTokenizer | AllFilters(PunctuationFilter, INTRAWORD) | SANITIZATION
)


class MySchema(SchemaClass):
    url = ID(stored=True, unique=True, field_boost=0.5)
    depth = NUMERIC(sortable=True)
    title = TEXT(
        stored=True,
        field_boost=1.5,
        analyzer=DEFAULT_ANALYZER,
    )
    content = TEXT(
        stored=True,
        chars=True,
        analyzer=DEFAULT_ANALYZER,
    )
    description = TEXT(
        stored=True,
        chars=True,
        analyzer=DEFAULT_ANALYZER,
    )
    created_at = DATETIME(stored=True, sortable=True)
    last_updated = DATETIME(stored=True, sortable=True)
    dead_since = DATETIME(stored=True, sortable=True)


class MyIndexWriter(SegmentWriter):
    @override
    def update_document(
        self,
        *,
        comparison_functions: Optional[Dict[str, callable]] = {},
        fields_if_exists: Optional[Dict[str, Any]] = None,
        **fields,
    ):
        """
        Works like usual with the exception of the `fields_if_exists` being a reserved keyword.

        Refer to `IndexWriter.update_document` for further documentation.

        Args:
            fields_if_exists (Optional[Dict[str, Any]], optional): If the document exists, these fields are used instead of the ones specified with keywords. Useful for fields like "created time". Defaults to None.
            comparison_functions (Optional[Dict[str, callable]], optional): If the document exists, use the functions in this dictionary to compare old/new values and pick one. The callable should take the old value as the first argument, and the new one as the second. It should return whichever value should be used.
        """
        assert sorted(list(fields)) == sorted(
            no_phrase_fields := [
                f for f in list(self.schema._fields) if not f.startswith("phrase_")
            ]
        ), f"You are missing the following fields: {list(set(no_phrase_fields).difference(set(fields)))}. You have the following extra fields: {list(set(fields).difference(set(no_phrase_fields)))}"
        # Delete the set of documents matching the unique terms
        unique_fields = self._unique_fields(fields)
        if unique_fields:
            with self.searcher() as s:
                uniqueterms = [(name, fields[name]) for name in unique_fields]
                docs = s._find_unique(uniqueterms)

                stored_fields = {}
                if docs:  # if it already exists
                    docnum = docs.pop()
                    with self.reader() as r:
                        stored_fields = {} or r.stored_fields(docnum)
                    self.delete_document(docnum)

                    fields.update(stored_fields)
                    if comparison_functions:
                        for field in comparison_functions:
                            func = comparison_functions[field]
                            original_value = fields[field]
                            new_value = fields_if_exists[field]
                            updated_value = func(original_value, new_value)
                            fields_if_exists[field] = updated_value
                    fields.update(fields_if_exists)

        for field in fields.copy():
            phrasename = f"phrase_{field}"
            if (phrasename in self.schema._fields) and (phrasename not in fields):
                fields[phrasename] = fields[field]

        # Add the given fields
        self.add_document(**fields)


class MySearcher(Searcher):
    """Returns results with `MyHighlighter` as the default highlighter."""

    @override
    def search(self, q, **kwargs):
        results = super().search(q, **kwargs)
        results.highlighter = MyHighlighter()
        return results

    @override
    def search_page(self, query, pagenum, pagelen=10, **kwargs):
        results = super().search_page(query, pagenum, pagelen, **kwargs)
        results.highlighter = MyHighlighter()
        return results


class MyFileIndex(FileIndex):
    @staticmethod
    def create_in(dirname, schema, indexname=None) -> "MyFileIndex":
        """Convenience function to create an index in a directory. Takes care of
        creating a FileStorage object for you.

        :param dirname: the path string of the directory in which to create the
            index.
        :param schema: a :class:`whoosh.fields.Schema` object describing the
            index's fields.
        :param indexname: the name of the index to create; you only need to specify
            this if you are creating multiple indexes within the same storage
            object.
        :returns: :class:`Index`
        """

        from whoosh.filedb.filestore import FileStorage
        from whoosh.index import _DEF_INDEX_NAME

        if not indexname:
            indexname = _DEF_INDEX_NAME
        storage = FileStorage(dirname)
        return MyFileIndex.create(storage, schema, indexname)

    @staticmethod
    def open_dir(dirname, indexname=None, readonly=False, schema=None):
        """Convenience function for opening an index in a directory. Takes care of
        creating a FileStorage object for you. dirname is the filename of the
        directory in containing the index. indexname is the name of the index to
        create; you only need to specify this if you have multiple indexes within
        the same storage object.

        :param dirname: the path string of the directory in which to create the
            index.
        :param indexname: the name of the index to create; you only need to specify
            this if you have multiple indexes within the same storage object.
        """

        from whoosh.filedb.filestore import FileStorage
        from whoosh.index import _DEF_INDEX_NAME

        if indexname is None:
            indexname = _DEF_INDEX_NAME
        storage = FileStorage(dirname, readonly=readonly)
        return MyFileIndex(storage, schema=schema, indexname=indexname)

    @overload
    def writer(
        self, procs: Union[Literal[0], Literal[1]] = 1, **kwargs
    ) -> MyIndexWriter: ...

    @override
    def writer(self, procs: int = 1, **kwargs) -> MpWriter:
        """
        Returns MyIndexWriter if `procs` is 1, otherwise returns `MpWriter` (as usual).
        """
        if procs > 1:
            from whoosh.multiproc import MpWriter

            return MpWriter(self, procs=procs, **kwargs)
        else:
            return MyIndexWriter(self, **kwargs)

    @override
    def searcher(self, **kwargs) -> MySearcher:
        return MySearcher(self.reader(), fromindex=self, **kwargs)

    def get_docnums_and_results(
        self, q: Query = None, limit: int = None
    ) -> Generator[Tuple[int, Dict[str, Any]], None, None] | None:
        """Returns a generator of tuples containing a result's docnum and its fields.

        If passed a query, the generator will only include results that match this query.
        If kwargs are present, they are passed to the query.

        If no query is present, the generator includes every result in the index.

        Args:
            q (Query, optional): The query to match results on. Defaults to None.
            limit (int, optional): The maximum amount of results to return. Defaults to as many as possible (no limit).

        Yields:
            Generator[Tuple[int, Dict[str, Any]], None, None] | None: Returns a generator of tuples containing a result's docnum and its fields
        """
        with self.searcher() as s:
            yield from (
                (
                    (docnum, s.ixreader.stored_fields(docnum))
                    for docnum in (
                        s.search(q, limit=limit or s.doc_count_all()).docs()
                        if q
                        else s.document_numbers()
                    )
                )
                if s.doc_count()
                else []
            )


def get_index(storage_path: Optional[str] = None, schema=MySchema) -> MyFileIndex:
    """Get a file index, based on either:
        1. The `INDEX_PATH` value in the scrapy project's settings.
        2. The path passed to the function (`storage_path`)

    If no path is given, `ValueError` is raised.
    If a path is given but it doesn't exist, the index is created at that path and returned.
    """
    if storage_path is None:
        from scrapy.utils.project import get_project_settings

        storage_path = get_project_settings().get("INDEX_PATH", None)
        if storage_path is None:
            raise ValueError(
                "Please define the `INDEX_PATH` value in the scrapy project's settings or pass the path to the function via `storage_path`."
            )
        storage_path = str(Path(storage_path).absolute())
    if not os.path.exists(storage_path):
        os.mkdir(storage_path)
        return MyFileIndex.create_in(storage_path, schema=schema())
    else:
        return MyFileIndex.open_dir(storage_path, schema=schema())


class MyFormatter(Formatter):
    def __init__(
        self,
        tagname="strong",
        between="...",
    ):
        self.tagname = tagname
        self.between = between

    @override
    def _text(self, text):
        return html_escape(text)

    @override
    def format_token(self, text, token, replace=False):
        ttext = self._text(get_text(text, token, replace))

        return f"<{self.tagname}>{html_escape(ttext)}</{self.tagname}>"


class MyHighlighter(Highlighter):
    def __init__(
        self,
        fragmenter=PinpointFragmenter(surround=25),
        scorer=None,
        formatter=MyFormatter(),
        always_retokenize=False,
        order=FIRST,
    ):
        super().__init__(fragmenter, scorer, formatter, always_retokenize, order)


def query_is_valid(node):
    if isinstance(node, (Every, _NullQuery)):
        return False
    # If the node has subqueries (e.g., a compound query), recursively check them
    subqueries = [subquery for subquery in node.children()]
    if not subqueries:
        return True
    return any(query_is_valid(subquery) for subquery in subqueries)


def search(search_term: str, storage_path: str, pagenum: int = 1):
    ix = get_index(storage_path)

    with ix.searcher() as searcher:
        from whoosh.qparser import (
            WildcardPlugin,
            GroupPlugin,
            OperatorsPlugin,
        )

        # for reference: https://whoosh-reloaded.readthedocs.io/en/latest/parsing.html#overview
        qp = SimpleParser(
            "content",
            schema=ix.schema,
            group=OrGroup.factory(0.9),
            phraseclass=Phrase,
            plugins=[
                WildcardPlugin(),
                GroupPlugin(),
                OperatorsPlugin(),
                OperatorsPlugin(
                    And=r"&", Or=r"\|", AndNot=r"&!", AndMaybe=r"&~", Not=None
                ),
                FieldsPlugin(),
            ],
        )
        query = qp.parse(search_term)
        if not query_is_valid(query):
            return {"valid": False}

        results_page = searcher.search_page(query, terms=True, pagenum=pagenum)
        results = results_page.results
        num_results = results.estimated_length()
        has_exact = results.has_exact_length()
        is_last = results_page.is_last_page()
        return {
            "valid": True,
            "results": [
                {
                    "url": hit["url"],
                    "title": hit["title"],
                    "depth": hit["depth"],
                    "snippet": hit.highlights("content", strict_phrase=True)
                    or hit["description"]
                    or hit["content"][:170],
                }
                for hit in results_page
            ],
            "duration": results.runtime,
            "total": num_results,
            "exact": has_exact,
            "last": is_last,
            "maxpage": results_page.pagecount,
        }
