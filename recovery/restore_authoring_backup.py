"""Verify or restore the optional private SHA-256-blob authoring archive."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(name):
    p = PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(x in {'.', '..'} or ':' in x or '\\' in x for x in p.parts):
        raise ValueError('Unsafe archive path')
    return Path(*p.parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive', type=Path, required=True)
    ap.add_argument('--sha256', required=True)
    ap.add_argument('--output', type=Path, help='New directory; omit to verify only')
    args = ap.parse_args()
    if sha(args.archive) != args.sha256.lower():
        raise ValueError('Archive SHA-256 mismatch')
    with zipfile.ZipFile(args.archive) as archive:
        manifest = json.loads(archive.read('AUTHORING_MANIFEST.json').decode('utf-8'))
        records = manifest['records']
        names = set()
        blobs = {}
        for r in records:
            relative(r['path'])
            if r['path'] in names or not re.fullmatch('[0-9a-f]{64}', r['sha256']):
                raise ValueError('Duplicate path or invalid content hash')
            names.add(r['path'])
            prior = blobs.setdefault(r['sha256'], r['bytes'])
            if prior != r['bytes']:
                raise ValueError('Conflicting blob length')
        expected = {'blobs/' + digest for digest in blobs} | {'AUTHORING_MANIFEST.json'}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError('Unexpected or duplicate archive entries')
        for digest, length in blobs.items():
            name = 'blobs/' + digest
            if archive.getinfo(name).file_size != length:
                raise ValueError('Blob size mismatch')
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    raise ValueError('Blob SHA-256 mismatch')
        if args.output:
            output = args.output.resolve()
            output.mkdir(parents=True, exist_ok=False)
            for index, r in enumerate(records):
                target = output / relative(r['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open('blobs/' + r['sha256']) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                if target.stat().st_size != r['bytes'] or sha(target) != r['sha256']:
                    raise ValueError('Restored file mismatch: ' + r['path'])
                if (index + 1) % 10000 == 0:
                    print('RESTORED_AND_VERIFIED', index + 1, flush=True)
    print(json.dumps({'status': 'PASS', 'records_verified': len(records), 'unique_blobs_verified': len(blobs),
                      'restored': args.output is not None, 'every_restored_file_sha256_verified': args.output is not None}))


if __name__ == '__main__':
    main()
