"""Validate optional local post evidence. Semantic reading stays with the Agent."""
import hashlib
import json
from pathlib import Path


def load_post_evidence(review_file, input_hash, topics):
    def check(ok, message):
        if not ok:
            raise ValueError(message)

    def text(value, name):
        check(isinstance(value, str) and bool(value.strip()), name + ' must be non-empty text')
        return value

    def read(path):
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def file_hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def locate(parent, name):
        name = Path(text(name, 'local file path'))
        return (parent / name).resolve()

    review_path = Path(review_file).resolve()
    review = read(review_path)
    check(isinstance(review, dict) and review.get('schema_version') in (1, 2), 'Invalid post review schema')
    check(review.get('input_sha256') == input_hash, 'Post review belongs to a different word input')
    source_path = locate(review_path.parent, review.get('posts_file'))
    check(file_hash(source_path) == review.get('posts_sha256'), 'Normalized posts hash mismatch')
    source = read(source_path)
    check(isinstance(source, dict) and source.get('schema_version') == review.get('schema_version'), 'Invalid posts schema')
    text(source.get('scope'), 'post scope')
    source_files = source.get('source_files')
    check(isinstance(source_files, list) and bool(source_files), 'Source file records are required')
    verified_sources = []
    for entry in source_files:
        check(isinstance(entry, dict), 'Invalid source file record')
        raw_path = locate(source_path.parent, entry.get('path'))
        check(file_hash(raw_path) == entry.get('sha256'), 'Original post source hash mismatch')
        verified_sources.append({'path': str(raw_path), 'sha256': entry['sha256']})
    rows = source.get('posts')
    check(isinstance(rows, list), 'posts must be a list')
    posts = {}
    for row in rows:
        check(isinstance(row, dict), 'Invalid post record')
        post_id = text(row.get('id'), 'post id')
        check(post_id not in posts, 'Duplicate post id: ' + post_id)
        text(row.get('text'), 'post text')
        text(row.get('source_locator'), 'source locator')
        check(row.get('scope_status') in ('in_scope', 'context_only', 'unknown'), 'Invalid scope status')
        posts[post_id] = row
    for key in ('selection_note', 'stopping_reason'):
        text(review.get(key), key)
    reads = review.get('read_posts')
    check(isinstance(reads, list), 'read_posts must be a list')
    checked_reads = []
    source_cache = {}
    schema = review['schema_version']
    seen = set()
    for entry in reads:
        check(isinstance(entry, dict), 'Invalid reading record')
        post_id = text(entry.get('post_id'), 'read post id')
        check(post_id in posts and post_id not in seen, 'Unknown or duplicate read post id: ' + post_id)
        seen.add(post_id)
        check(entry.get('phase') in ('initial', 'supplemental'), 'Invalid reading phase')
        text(entry.get('note'), 'reading note')
        checked = dict(entry, source_locator=posts[post_id]['source_locator'], scope_status=posts[post_id]['scope_status'])
        if schema == 1:
            excerpt = text(entry.get('excerpt'), 'excerpt')
            check(excerpt in posts[post_id]['text'], 'Excerpt not found in original post: ' + post_id)
        else:
            from field_source import resolve_field
            mappings = posts[post_id].get('field_sources')
            check(isinstance(mappings, dict), 'schema2 requires field_sources')
            body, body_key = resolve_field(mappings.get('text'), verified_sources, source_cache)
            check(body == posts[post_id]['text'], 'Normalized body differs from original source: ' + post_id)
            fragments = entry.get('evidence')
            check(isinstance(fragments, list) and bool(fragments), 'schema2 requires evidence fragments')
            checked_fragments = []
            for fragment in fragments:
                check(isinstance(fragment, dict), 'Evidence fragment must be an object')
                field = fragment.get('field')
                check(field in ('text', 'title', 'ocr_text'), 'Unknown evidence field')
                value = text(posts[post_id].get(field), 'post.' + field)
                excerpt = text(fragment.get('excerpt'), 'excerpt')
                text(fragment.get('claim'), 'claim')
                check(fragment.get('relation') in ('supports', 'contradicts', 'qualifies', 'context'), 'Unknown evidence relation')
                raw_value, record_key = resolve_field(mappings.get(field), verified_sources, source_cache)
                check(raw_value == value, 'Normalized field differs from original source: ' + post_id + '/' + field)
                if body_key is not None:
                    check(record_key == body_key, 'Post fields come from different source rows')
                check(excerpt in value, 'Excerpt not found in specified field: ' + post_id + '/' + field)
                checked_fragments.append(dict(fragment, source=mappings[field]))
            checked['evidence'] = checked_fragments
        checked_reads.append(checked)
    for topic in topics:
        ids = topic.get('post_ids', [])
        check(isinstance(ids, list) and all(isinstance(i, str) for i in ids), 'post_ids must be strings')
        check(len(ids) == len(set(ids)), 'Duplicate topic post citation')
        check(all(i in seen for i in ids), 'Topic cites an unread or unknown post')
        check(all(posts[i]['scope_status'] != 'unknown' for i in ids), 'Topic cites a post with unverified scope')
    return {'schema_version': schema, 'review_file': str(review_path), 'review_sha256': file_hash(review_path),
            'posts_file': str(source_path), 'posts_sha256': file_hash(source_path),
            'source_files': verified_sources, 'scope': source['scope'], 'available_posts': len(posts),
            'unique_read_posts': len(seen), 'selection_note': review['selection_note'],
            'read_posts': checked_reads, 'stopping_reason': review['stopping_reason'],
            'validation': ('schema2: raw field locations, values, excerpts, IDs and hashes checked' if schema == 2 else 'legacy schema1: body excerpts, IDs and hashes only; field grounding not checked') + '; actual reading and semantic support require Agent review'}
