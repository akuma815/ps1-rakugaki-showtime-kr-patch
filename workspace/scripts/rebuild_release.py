"""Rebuild the frozen movies-20260811 release from owned media and source-referenced movie deltas."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check(path, expected):
    if sha(path).lower() != expected.lower():
        raise ValueError('Input/output hash mismatch: ' + str(path))


def run(args, cwd, log):
    with log.open('wb') as out:
        result = subprocess.run([str(x) for x in args], cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('Build failed; see ' + str(log))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-bin', type=Path, required=True)
    ap.add_argument('--source-cue', type=Path, required=True)
    ap.add_argument('--font-dir', type=Path, required=True)
    ap.add_argument('--xdelta', type=Path, required=True)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    profile = json.loads((ROOT / 'assets/rebuild/profile.json').read_text(encoding='utf-8'))
    source = profile['source']
    check(args.source_bin, source['bin_sha256'])
    check(args.source_cue, source['cue_sha256'])
    if args.source_bin.stat().st_size != source['bin_size']:
        raise ValueError('Original BIN size mismatch')
    for font in profile['fonts']:
        check(args.font_dir / font['name'], font['sha256'])
    manifest = ROOT / 'REBUILD_INPUT_MANIFEST.json'
    if manifest.exists():
        for record in json.loads(manifest.read_text(encoding='utf-8'))['records']:
            check(ROOT / record['path'], record['sha256'])
    stage = (args.output or ROOT / 'build' / ('rebuild_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))).resolve()
    stage.mkdir(parents=True, exist_ok=False)
    shutil.copytree(ROOT / 'workspace', stage / 'workspace', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / 'assets/rebuild', stage / 'assets/rebuild')
    (stage / 'assets/fonts').mkdir()
    for font in profile['fonts']:
        shutil.copyfile(args.font_dir / font['name'], stage / 'assets/fonts' / font['name'])
    for name in ['bin', 'cue']:
        target = stage / source[name]
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(getattr(args, 'source_' + name), target)
    original = stage / source['bin']
    original_cue = stage / source['cue']
    (stage / 'work/analysis').mkdir(parents=True)
    for name in ['font_coverage_001.json', 'menu_strips_catalog.json']:
        shutil.copyfile(ROOT / 'assets/rebuild' / name, stage / 'work/analysis' / name)
    from psx_disc import inspect_iso, extract_files, expected_msf
    from psx_edc_ecc import verify_mode2_form1
    from merge_psx_movie_video import collect_video_frames, split_sectors
    from build_hangul_atlas_movie import diff_image_sectors
    extract_files(original, inspect_iso(original, hash_files=False)['entries'], {'ARA.BIN', 'HAGECHO.BIN'}, stage / 'extracted')
    movies = []
    movie_lbas = set()
    for item in profile['movies']:
        name = item['name']
        check(ROOT / item['patch'], item['patch_sha256'])
        check(ROOT / item['subtitle'], item['subtitle_sha256'])
        with original.open('rb') as stream:
            stream.seek(item['lba'] * 2352)
            before = stream.read(item['sector_count'] * 2352)
        raw = stage / (name + '_original.STR')
        raw.write_bytes(before)
        check(raw, item['original_sha256'])
        target = stage / (name + '_KR.STR')
        run([args.xdelta.resolve(), '-d', '-s', raw, (ROOT / item['patch']).resolve(), target], stage, stage / (name + '_restore.log'))
        check(target, item['target_sha256'])
        after = target.read_bytes()
        if len(before) != len(after):
            raise ValueError(name + ': movie extent size changed')
        old = split_sectors(before)
        new = split_sectors(after)
        old_frames = collect_video_frames(old)
        new_frames = collect_video_frames(new)
        if sorted(old_frames) != sorted(new_frames):
            raise ValueError(name + ': frame number set changed')
        video = {chunk['sector_index'] for frame in old_frames.values() for chunk in frame.values()}
        changed = [i for i, pair in enumerate(zip(old, new, strict=True)) if pair[0] != pair[1]]
        if not set(changed) <= video:
            raise ValueError(name + ': XA or other non-video bytes changed')
        for i, sector in enumerate(new):
            if sector[:24] != old[i][:24] or sector[12:15] != expected_msf(item['lba'] + i):
                raise ValueError(name + ': raw header changed')
            if i in video and not verify_mode2_form1(sector):
                raise ValueError(name + ': video EDC/ECC invalid')
        lbas = {item['lba'] + i for i in changed}
        if lbas & movie_lbas:
            raise ValueError('Overlapping movie writes')
        movie_lbas |= lbas
        movies.append({**item, 'changed_indices':changed, 'sectors':new})
        print('MOVIE_VERIFIED', name, len(changed), flush=True)
    outputs = {}
    for variant in ['cheat', 'no_cheat']:
        command = [sys.executable, '-X', 'utf8', 'workspace/scripts/build_hangul_atlas.py']
        if variant == 'no_cheat':
            command.append('--no-cheats')
        run(command, stage, stage / ('base_' + variant + '.log'))
        base_id = 'hangul_atlas_001' if variant == 'cheat' else 'hangul_atlas_no_cheat_001'
        base_report = json.loads((stage / 'work/analysis' / (base_id + '_build.json')).read_text(encoding='utf-8'))
        base_bin = stage / base_report['output']['bin']
        check(base_bin, profile['base_expected'][variant])
        writes = base_report['expected_writes']['sectors']
        base_lbas = {int(r['lba']) for r in writes}
        if len(base_lbas) != len(writes) or diff_image_sectors(original, base_bin) != base_lbas:
            raise ValueError('Base Expected Write plan differs from actual changes')
        if base_lbas & movie_lbas:
            raise ValueError('Base/movie writers overlap')
        final = stage / 'final' / Path(profile['final_expected'][variant]['target']).name
        final.parent.mkdir(exist_ok=True)
        shutil.copyfile(base_bin, final)
        with final.open('r+b') as stream:
            for movie in movies:
                for i in movie['changed_indices']:
                    stream.seek((movie['lba'] + i) * 2352)
                    stream.write(movie['sectors'][i])
        if final.stat().st_size != source['bin_size'] or diff_image_sectors(original, final) != base_lbas | movie_lbas:
            raise ValueError('Final Expected Write plan differs from actual changes')
        check(final, profile['final_expected'][variant]['target_sha256'])
        cue, count = re.subn(r'(?im)^FILE\s+"[^\"]+"\s+BINARY$', f'FILE "{final.name}" BINARY', original_cue.read_text(encoding='ascii'))
        if count != 1:
            raise ValueError('Unexpected original CUE layout')
        final.with_suffix('.cue').write_text(cue, encoding='ascii', newline='')
        check(final.with_suffix('.cue'), profile['final_expected'][variant]['cue_sha256'])
        outputs[variant] = {'bin':str(final.relative_to(stage)), 'bytes':final.stat().st_size, 'sha256':sha(final),
                            'cue_sha256':sha(final.with_suffix('.cue')), 'base_sectors':len(base_lbas), 'movie_sectors':len(movie_lbas)}
    run([sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'workspace/tests'], stage, stage / 'unit_tests.log')
    report = {'status':'PASS', 'evidence_class':'Source base builds plus frozen encoded-video delta restoration, full output hashes and write-plan checks; no new runtime review',
        'environment':{'python':platform.python_version()}, 'movie_count':len(movies), 'original_xa_preserved':True,
        'all_video_edc_ecc_verified':True, 'final_write_plans_verified':True,
        'intermediate_movies_reencoded':False, 'unit_tests_exit_code':0, 'new_runtime_test':False, 'outputs':outputs}
    (stage / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output_directory':str(stage), **report}, indent=2))


if __name__ == '__main__':
    main()
