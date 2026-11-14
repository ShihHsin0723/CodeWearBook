"""Offline checks for the effective retrieval controls, without API startup."""
import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest

from langchain.schema import Document
from retrieval_settings import RetrievalSettings
from scripts.eval.run_retrieval_sensitivity import configurations

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, namespace):
    tree = ast.parse((ROOT/path).read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace


class RetrievalSensitivityTests(unittest.TestCase):
    def test_schema_keep_changes_without_changing_candidate_search(self):
        docs = [Document(page_content=str(i), metadata={'type':'schema'}) for i in range(32)]
        seen = []
        vectorstore = SimpleNamespace(similarity_search=lambda query,k: (seen.append(k) or docs[:k]))
        ns = functions('embedding.py', {'get_relevant_chunks_by_type'}, {})
        for keep in (4,8,12):
            returned = ns['get_relevant_chunks_by_type']('query', SimpleNamespace(vectorstore=vectorstore),type_filter='schema',k=keep,search_k=32)
            self.assertEqual(len(returned),keep)
        self.assertEqual(seen,[32,32,32])

    def test_kb_restores_parents_and_limits_after_table_filtering(self):
        docs = [Document(page_content='chunk',metadata={'type':'information','id':str(i),'_parent_doc_key':str(i),'_full_page_content':f'full {i}'}) for i in range(12)]
        docs.insert(0, docs[0])  # Duplicate chunks must not consume retained-document slots.
        seen = []
        vectorstore = SimpleNamespace(similarity_search=lambda query,k: (seen.append(k) or docs[:k]))
        ns = functions('embedding.py', {'_restore_full_documents'}, {'Document':Document})
        ns.update(RETRIEVAL_SETTINGS=RetrievalSettings(),_kb_doc_matches_selected_tables=lambda doc,tables: int(doc.metadata['id']) >= 2)
        functions('query_system.py', {'_get_semantic_schema_matched_kb_docs'}, ns)
        for keep in (3,5,8):
            ns['RETRIEVAL_SETTINGS'] = replace(RetrievalSettings(),kb_keep=keep)
            result = ns['_get_semantic_schema_matched_kb_docs']('query',SimpleNamespace(vectorstore=vectorstore),['bump_table'])
            self.assertEqual(len(result),keep)
            self.assertEqual(result[0].page_content,'full 2')
        self.assertEqual(seen,[40,40,40])
        self.assertEqual(ns['_get_semantic_schema_matched_kb_docs']('query',SimpleNamespace(vectorstore=vectorstore),[]),[])
        self.assertEqual(seen,[40,40,40])

    def test_default_candidate_search_preserves_existing_helper_behavior(self):
        seen = []
        retriever = SimpleNamespace(vectorstore=SimpleNamespace(similarity_search=lambda query,k: (seen.append(k) or [])))
        ns = functions('embedding.py', {'get_relevant_chunks_by_type'}, {})
        ns['get_relevant_chunks_by_type']('q',retriever,type_filter='schema',k=8)
        self.assertEqual(seen,[32])

    def test_one_factor_design_and_fixed_search_counts(self):
        baseline = RetrievalSettings()
        from dataclasses import asdict
        for name, settings in configurations().items():
            changed = [key for key,value in asdict(settings).items() if value != asdict(baseline)[key]]
            self.assertEqual(len(changed),0 if name=='baseline' else 1)
            self.assertEqual((settings.schema_search,settings.kb_search),(32,40))
        with self.assertRaises(ValueError):
            replace(baseline,kb_keep=41)
        with self.assertRaises(ValueError):
            replace(baseline,chunk_overlap=400)


if __name__=='__main__':
    unittest.main()
