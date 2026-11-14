#!/usr/bin/env python3
"""Run a resumable, one-factor-at-a-time BUMP retrieval sensitivity experiment."""
import argparse
import contextlib
from dataclasses import asdict, replace
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import subprocess
import sys
from statistics import fmean, stdev

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from retrieval_settings import RetrievalSettings


def configurations():
    base = RetrievalSettings()
    return {
        'baseline': base,
        'chunk_200': replace(base, chunk_size=200),
        'chunk_800': replace(base, chunk_size=800),
        'overlap_0': replace(base, chunk_overlap=0),
        'overlap_100': replace(base, chunk_overlap=100),
        'schema_4': replace(base, schema_keep=4),
        'schema_12': replace(base, schema_keep=12),
        'kb_3': replace(base, kb_keep=3),
        'kb_8': replace(base, kb_keep=8),
    }


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, obj):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def prepare(args):
    from langchain_text_splitters import CharacterTextSplitter
    from kb_registry import build_scoped_kb_documents
    from generate_schema import generate_schema_description
    from database_guardrails import create_read_only_sqlite_engine
    from config import SHARED_SQLITE_PATH, DB_CONNECTION_STRING
    logging.getLogger('langchain_text_splitters.base').setLevel(logging.ERROR)
    engine = create_read_only_sqlite_engine(DB_CONNECTION_STRING)
    schema = [d for d in generate_schema_description(engine) if d.metadata.get('table', '').startswith('bump_')]
    engine.dispose()
    kb = build_scoped_kb_documents('BUMP')
    if not schema or not kb:
        raise ValueError('Missing BUMP schema or KB')
    corpora = {'schema': schema, 'kb': kb}
    checks, signatures = {}, {}
    for name, setting in configurations().items():
        checks[name], signatures[name] = {}, []
        for corpus, docs in corpora.items():
            chunks = CharacterTextSplitter(chunk_size=setting.chunk_size, chunk_overlap=setting.chunk_overlap).split_documents(docs)
            contents = [(c.page_content, c.metadata) for c in chunks]
            signature = hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest()
            signatures[name].append(signature)
            checks[name][corpus] = {'parents': len(docs), 'chunks': len(chunks), 'over_target': sum(len(c.page_content) > setting.chunk_size for c in chunks), 'max_chars': max(len(c.page_content) for c in chunks), 'content_sha256': signature}
    skipped = {name: 'Identical schema and KB chunks and identical retrieval limits to baseline'
               for name, settings in configurations().items()
               if name != 'baseline' and signatures[name] == signatures['baseline']
               and (settings.schema_keep, settings.kb_keep) == (8, 5)}
    rows = [json.loads(line) for line in args.prompts.read_text().splitlines() if line.strip()]
    # Validate all reference queries before spending on generation; only schema/SQL reads.
    engine = create_read_only_sqlite_engine(DB_CONNECTION_STRING)
    from sqlalchemy import text
    with engine.connect() as connection:
        for row in rows:
            connection.execute(text('EXPLAIN QUERY PLAN ' + row['sql'])).fetchall()
    engine.dispose()
    tracked = ['query_system.py', 'embedding.py', 'retrieval_settings.py', 'llm.py', 'config.py',
               'kb_registry.py', 'knowledge_base_context.py', 'generate_schema.py',
               'scripts/eval/run_retrieval_sensitivity.py', 'scripts/eval/prompt_eval_utils.py',
               'scripts/eval/test_prompts_with_ambiguity_replay.py', 'scripts/eval/test_simulated_prompts.py',
               'database_guardrails.py', 'dataset_registry.py', 'dataset_plugins.py']
    fingerprint = {'prompt_sha256': digest(args.prompts), 'code_sha256': {p: digest(ROOT/p) for p in tracked},
                   'database': {'path': str(SHARED_SQLITE_PATH), 'size': SHARED_SQLITE_PATH.stat().st_size,
                                'mtime_ns': SHARED_SQLITE_PATH.stat().st_mtime_ns},
                   'chunk_checks': checks, 'settings': {n: asdict(s) for n, s in configurations().items()},
                   'repetitions': args.repetitions, 'seed': args.seed}
    manifest = {'fingerprint': fingerprint, 'prompts': str(args.prompts.resolve()), 'questions': len(rows),
                'skipped': skipped, 'clarification_policy': 'Recompute table resolution; auto-select option 1 if clarification needed; no reference-SQL-derived table choices',
                'scoring': 'Curated canonical SQL equality or dataframe column alignment and row-multiset similarity; failures score zero',
                'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'status': 'prepared'}
    manifest_path = args.output/'manifest.json'
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous['fingerprint'] != fingerprint:
            raise ValueError('Existing manifest differs; use a new output directory')
        manifest = previous
    else:
        write_json(manifest_path, manifest)
    return rows, manifest


def report(args, manifest):
    from scripts.eval.summarize_efficiency import summarize_rows, DEFAULT_PRICES
    from scripts.eval.summarize_uncertainty import bootstrap_mean_interval
    prices = json.loads(DEFAULT_PRICES.read_text())
    # Price file has metadata; the efficiency CLI uses this same nested mapping.
    prices = prices.get('models', prices)
    aggregates, per_question, per_question_exact = {}, {}, {}
    for name in configurations():
        if name in manifest['skipped']:
            continue
        runs = []
        for repetition in range(1, args.repetitions+1):
            path = args.output/f'{name}_r{repetition}.jsonl'
            if not path.exists():
                continue
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if len(rows) != manifest['questions']:
                continue
            runs.append(rows)
        if len(runs) != args.repetitions:
            continue
        scores = [fmean(r['df_match_score'] for r in rows) for rows in runs]
        exact = [fmean(bool(r['dataframe_match']) for r in rows) for rows in runs]
        per_question[name] = [fmean(rows[i]['df_match_score'] for rows in runs) for i in range(manifest['questions'])]
        per_question_exact[name] = [fmean(bool(rows[i]['dataframe_match']) for rows in runs) for i in range(manifest['questions'])]
        aggregates[name] = {'mean_similarity': fmean(scores), 'similarity_run_sd': stdev(scores) if len(scores)>1 else None,
                            'exact_match_rate': fmean(exact), 'exact_match_run_sd': stdev(exact) if len(exact)>1 else None,
                            'per_run_similarity': scores, 'per_run_exact_match': exact,
                            'efficiency': summarize_rows([r for rows in runs for r in rows], prices)}
    if 'baseline' in per_question:
        for name, scores in per_question.items():
            differences = [s-b for s,b in zip(scores, per_question['baseline'])]
            aggregates[name]['similarity_delta_pp'] = 100*fmean(differences)
            aggregates[name]['paired_question_bootstrap_95_ci_pp'] = [100*x for x in bootstrap_mean_interval(differences)]
            exact_differences = [s-b for s,b in zip(per_question_exact[name], per_question_exact['baseline'])]
            aggregates[name]['exact_match_delta_pp'] = 100*fmean(exact_differences)
            aggregates[name]['exact_match_paired_question_bootstrap_95_ci_pp'] = [100*x for x in bootstrap_mean_interval(exact_differences)]
    write_json(args.output/'summary.json', aggregates)
    lines = ['# BUMP retrieval sensitivity results', '',
             'Current-system end-to-end benchmark; original manuscript results are not reproduced automatically.', '',
             '| Configuration | Mean similarity (%) | Exact match (%) | Similarity change (pp) | Run SD (pp) | Failures | Mean latency (s) | Mean input tokens |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, item in aggregates.items():
        e = item['efficiency']
        sd = item['similarity_run_sd']
        latency = e.get('query_latency_mean_seconds')
        calls = [json.loads(line) for repetition in range(1,args.repetitions+1)
                 for line in (args.output/f'{name}_r{repetition}.jsonl').read_text().splitlines() if line.strip()]
        tokens = fmean(sum((c.get('input_tokens') or 0)+(c.get('cache_creation_input_tokens') or 0)+(c.get('cache_read_input_tokens') or 0) for c in r['llm_calls']) for r in calls) if all(r.get('llm_usage_complete') for r in calls) else None
        sd_text = f"{100*sd:.2f}" if sd is not None else '—'
        latency_text = f"{latency:.2f}" if latency is not None else '—'
        tokens_text = f"{tokens:.0f}" if tokens is not None else '—'
        lines.append(f"| {name} | {100*item['mean_similarity']:.2f} | {100*item['exact_match_rate']:.2f} | {item.get('similarity_delta_pp',0):+.2f} | {sd_text} | {e['errors']} | {latency_text} | {tokens_text} |")
    lines += ['', 'Intervals bootstrap paired question-level scores averaged across repetitions; they are conditional on these runs, exploratory, and unadjusted for multiple comparisons.', '', 'Skipped equivalent settings: ' + json.dumps(manifest['skipped']), '']
    (args.output/'REPORT.md').write_text('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompts', type=Path, default=ROOT/'prompts/curated/bump_prompts.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT/'scripts/eval/results/sensitivity/bump_retrieval_20261007')
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--summarize-only', action='store_true')
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('repetitions must be positive')
    args.output.mkdir(parents=True, exist_ok=True)
    rows, manifest = prepare(args)
    print(f"Prepared {len(rows)} questions; skipped {list(manifest['skipped'])}", flush=True)
    if args.prepare_only:
        return
    if args.summarize_only:
        report(args, manifest)
        return
    path = args.output/'manifest.json'
    try:
        if manifest.get('model'):
            os.environ['ANTHROPIC_MODEL'] = manifest['model']
        import query_system as uqs
        from llm import _resolve_anthropic_model
        from scripts.eval.test_prompts_with_ambiguity_replay import run_case_with_replay
        manifest['model'] = _resolve_anthropic_model()
        os.environ['ANTHROPIC_MODEL'] = manifest['model']
        manifest['status'] = 'running'
        write_json(path, manifest)
        engine = uqs.create_shared_db_engine()
        index_cache = {}
        # Full baseline first, then randomized settings within each repetition.
        schedule = [('baseline',1)]
        rng = random.Random(args.seed)
        for repetition in range(1, args.repetitions+1):
            names = [n for n in configurations() if n not in manifest['skipped'] and (n,repetition) != ('baseline',1)]
            rng.shuffle(names)
            schedule.extend((name,repetition) for name in names)
        for name, repetition in schedule:
            setting = configurations()[name]
            result_path = args.output/f'{name}_r{repetition}.jsonl'
            existing = [json.loads(line) for line in result_path.read_text().splitlines() if line.strip()] if result_path.exists() else []
            for i, result in enumerate(existing):
                if result['index'] != i or result['question'] != rows[i]['question'] or result['retrieval_settings'] != asdict(setting):
                    raise ValueError('Result resume mismatch')
            if len(existing) == len(rows):
                continue
            uqs.RETRIEVAL_SETTINGS = setting
            key = (setting.chunk_size, setting.chunk_overlap)
            if key not in index_cache:
                print(f'Building indexes: {name}', flush=True)
                index_cache[key] = uqs.initialize_retrieval_context(engine, uqs.SCOPE_BUMP)
            context = index_cache[key]
            uqs.ALL_SCHEMA_DOCS = context['schema_docs']
            uqs.ALL_KNOWLEDGE_DOCS = context['kb_docs']
            for i in range(len(existing),len(rows)):
                log_path = args.output/f'{name}_r{repetition}.log'
                with log_path.open('a') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    result = run_case_with_replay(rows[i],i,engine,context['schema_retriever'],context['kb_retriever'],uqs.SCOPE_BUMP,
                                                  {'version':1,'choices':{}},args.output/'unused_choices.json',
                                                  record_choices=False,manual_ambiguity=False,force_prompt=False,quiet=False)
                calls = result.get('llm_calls',[])
                if any(c.get('error') for c in calls):
                    raise RuntimeError(f'Provider call failed at {name} repetition {repetition} question {i}; inspect local log. No infrastructure failure saved as an accuracy result.')
                if not result.get('generated_sql') or result.get('generated_df_preview', {}).get('row_count') is None:
                    result['error'] = result.get('error') or 'No executable SQL returned'
                if result['error']:
                    result['df_match_score'], result['dataframe_match'], result['sql_match'] = 0.0, False, False
                result.update(retrieval_settings=asdict(setting),configuration=name,repetition=repetition)
                with result_path.open('a') as f:
                    f.write(json.dumps(result,allow_nan=False)+'\n')
                print(f'{name} r{repetition} [{i+1}/{len(rows)}] similarity={result["df_match_score"]:.3f} error={bool(result["error"])}',flush=True)
            report(args,manifest)
        engine.dispose()
        manifest['status'] = 'complete'
        write_json(path,manifest)
        report(args,manifest)
        print('Experiment complete',flush=True)
    except Exception as exc:
        manifest['status'] = 'stopped'
        manifest['stop_reason'] = f'{type(exc).__name__}: {exc}'
        write_json(path,manifest)
        print(f'Experiment stopped: {type(exc).__name__}. Details in manifest.json.',flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
