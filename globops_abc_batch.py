#!/usr/bin/env python3
"""
globops_abc_batch.py -- Global Operations (LithTech ABC v108) viewmodels  ->  Source (CS:S) SMD + QC, in batch

    py globops_abc_batch.py  <folder-with-ABCs | one.abc>  [output-folder]  [options]

Examples
    py globops_abc_batch.py "C:\\GlobOps\\models\\v_models" "C:\\out"
    py globops_abc_batch.py "C:\\GlobOps\\models\\v_models" "C:\\out" --mirror
    py globops_abc_batch.py V_ANACONDA.ABC out --mirror --model v_anaconda=weapons/v_pist_deagle.mdl
    py globops_abc_batch.py "C:\\GlobOps\\models" out --info            (only list what is inside)

Every gun gets its OWN folder  <output>/<gun>/  holding everything needed to compile it:
    <gun>.qc          the QC (with $origin, bodygroups, attachments, sequences)
    body.smd          the always-visible parts
    parts/*.smd       optional parts (silencer, carry handle ...) and the alternate hand meshes
    anims/*.smd       one SMD per animation, resampled to --fps
    .abc_export       marker file: this folder belongs to the tool and is rewritten on the next run
Re-running a gun rewrites its folder from scratch (the old QC is kept as <gun>.qc.bak), so old and new
files can never get mixed. A folder without the marker is never touched unless you pass --force.

Left/right (cl_righthand):  Source flips a viewmodel at run time depending on BuiltRightHanded in the weapon
script. If a gun loads from the wrong side in-game, EITHER set "BuiltRightHanded" "1" in that weapon script
OR export that gun with --mirror (all guns) / --mirror-only name,name (some guns). Never do both.

Only tested on V_M4CARBINE.ABC. Files whose layout differs are reported as FAILED (the reader checks that every
section ends exactly where the file says it should) and the batch carries on with the next gun.
"""
import argparse, fnmatch, math, os, re, shutil, struct, sys
from dataclasses import dataclass, field

LOCAL_SCALE = 5.0            # weight / animation / socket locations are stored at 1/5 of model units
EYE_LT = (4.0, 14.0, 6.0)    # camera position (right, up, forward) in game units, from a hand-tuned $origin 3 -2 7 at scale 0.5
DEFAULT_OFF = {'handle', 'silencer', 'clip1'}
HAND_RE = re.compile(r'^([a-z])hand([lr])', re.I)
MARKER = '.abc_export'


class AbcError(Exception):
    pass


# ============================================================================= reader
class Reader:
    def __init__(self, data): self.d, self.o = data, 0
    def u(self, fmt):
        n = struct.calcsize('<' + fmt)
        if self.o + n > len(self.d): raise AbcError('unexpected end of file')
        v = struct.unpack_from('<' + fmt, self.d, self.o); self.o += n
        return v if len(v) > 1 else v[0]
    def s(self):
        n = self.u('H')
        if self.o + n > len(self.d): raise AbcError('unexpected end of file (string)')
        v = self.d[self.o:self.o + n].decode('latin1'); self.o += n; return v
    def skip(self, n): self.o += n


@dataclass
class Weight: node: int; loc: tuple; bias: float
@dataclass
class Vertex: weights: list; pos: tuple; normal: tuple
@dataclass
class Piece: name: str; material: int; faces: list; verts: list
@dataclass
class Node: name: str; index: int; bind: list; child_count: int; parent: int = -1
@dataclass
class Anim: name: str; keyframes: list; tf: list          # tf[node][keyframe] = (loc, quat xyzw)
@dataclass
class Socket: node: int; name: str; quat: tuple; loc: tuple
@dataclass
class Model:
    name: str = ''; node_count: int = 0; lod_count: int = 0
    pieces: list = field(default_factory=list); nodes: list = field(default_factory=list)
    anims: list = field(default_factory=list); sockets: list = field(default_factory=list)


def read_abc(path):
    data = open(path, 'rb').read()
    if len(data) < 32: raise AbcError('file too small')
    r = Reader(data)
    m = Model(name=os.path.splitext(os.path.basename(path))[0].lower())
    secs, off = [], 0
    while off != -1:                                           # walk the section chain first
        if off < 0 or off + 2 > len(data): raise AbcError('corrupt section chain (not an ABC file?)')
        r.o = off; name = r.s(); nxt = r.u('i')
        if nxt != -1 and nxt <= off: raise AbcError('corrupt section chain')
        secs.append((name, r.o, nxt if nxt != -1 else len(data))); off = nxt
    names = [s[0] for s in secs]
    for need in ('Header', 'Pieces', 'Nodes', 'Animation'):
        if need not in names: raise AbcError("missing '%s' section" % need)
    for name, start, end in secs:
        r.o = start
        if name == 'Header':
            ver = r.u('I')
            if ver != 108: raise AbcError('ABC version %d (this tool only reads v108)' % ver)
            r.skip(8); m.node_count = r.u('I'); r.skip(20); m.lod_count = r.u('I')
            if not (0 < m.node_count < 1000 and 0 < m.lod_count < 16): raise AbcError('implausible header values')
        elif name == 'Pieces':
            r.skip(4); pc = r.u('I')
            for _ in range(pc):
                mat = r.u('H'); r.skip(6 + 12 + 2); pname = r.s(); lods = []
                for _ in range(m.lod_count):
                    fc = r.u('I')
                    if r.o + fc * 30 > len(data): raise AbcError('face data runs past end of file')
                    flat = struct.unpack_from('<' + '2fH2fH2fH' * fc, data, r.o); r.o += fc * 30
                    faces = [[(flat[k*9+0], flat[k*9+1], flat[k*9+2]), (flat[k*9+3], flat[k*9+4], flat[k*9+5]),
                              (flat[k*9+6], flat[k*9+7], flat[k*9+8])] for k in range(fc)]
                    verts = []
                    for _ in range(r.u('I')):
                        wc = r.u('H'); r.skip(2)
                        ws = [Weight(r.u('I'), r.u('3f'), r.u('f')) for _ in range(wc)]
                        verts.append(Vertex(ws, r.u('3f'), r.u('3f')))
                    lods.append((faces, verts))
                faces, verts = lods[0]                         # LOD 0 only
                for f in faces:
                    for c in f:
                        if c[2] >= len(verts): raise AbcError("piece '%s': face index out of range" % pname)
                m.pieces.append(Piece(pname, mat, faces, verts))
            if r.o != end: raise AbcError("layout mismatch in 'Pieces' (ends at %d, expected %d)" % (r.o, end))
        elif name == 'Nodes':
            r.skip(4)
            for _ in range(m.node_count):
                nm = r.s(); idx = r.u('H'); r.skip(1 + 4)
                mat = r.u('16f'); cc = r.u('I')
                m.nodes.append(Node(nm, idx, [mat[i*4:i*4+4] for i in range(4)], cc))
            if r.o != end: raise AbcError("layout mismatch in 'Nodes' (ends at %d, expected %d)" % (r.o, end))
            stack = []
            for n in m.nodes:                                  # child_count = pre-order DFS encoding
                while stack and stack[-1][1] == 0: stack.pop()
                if stack: n.parent = stack[-1][0]; stack[-1][1] -= 1
                if n.child_count: stack.append([n.index, n.child_count])
        elif name == 'Animation':
            for _ in range(r.u('I')):
                r.skip(12); an = r.s(); r.skip(8); kc = r.u('I')
                kfs = [(r.u('I'), r.s()) for _ in range(kc)]
                tf = []
                need = m.node_count * (4 + kc * 36)
                if r.o + need > len(data): raise AbcError("animation '%s' runs past end of file" % an)
                for _ in range(m.node_count):
                    r.skip(4)
                    flat = struct.unpack_from('<' + '7f8x' * kc, data, r.o); r.o += kc * 36
                    tf.append([(flat[k*7:k*7+3], flat[k*7+3:k*7+7]) for k in range(kc)])
                if kc == 0: raise AbcError("animation '%s' has no keyframes" % an)
                m.anims.append(Anim(an, kfs, tf))
            if r.o != end: raise AbcError("layout mismatch in 'Animation' (ends at %d, expected %d)" % (r.o, end))
        elif name == 'Sockets':
            for _ in range(r.u('I')):
                ni = r.u('I'); nm = r.s(); q = r.u('4f'); l = r.u('3f'); m.sockets.append(Socket(ni, nm, q, l))
            if r.o != end: raise AbcError("layout mismatch in 'Sockets' (ends at %d, expected %d)" % (r.o, end))
    if len(m.nodes) != m.node_count: raise AbcError('node count mismatch')
    for p in m.pieces:
        for v in p.verts:
            for w in v.weights:
                if w.node >= m.node_count: raise AbcError("piece '%s': weight refers to a missing bone" % p.name)
    return m


# ============================================================================= math
def mmul(a, b): return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
def mT(a): return [[a[j][i] for j in range(3)] for i in range(3)]
def mv(a, v): return tuple(sum(a[i][k]*v[k] for k in range(3)) for i in range(3))
def sub(a, b): return tuple(x-y for x, y in zip(a, b))
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def dot(a, b): return sum(x*y for x, y in zip(a, b))

def quat_to_mat(q):
    x, y, z, w = q
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]

def slerp(a, b, t):
    d = sum(x*y for x, y in zip(a, b))
    if d < 0: b = tuple(-x for x in b); d = -d
    if d > 0.9995: q = tuple(x+(y-x)*t for x, y in zip(a, b))
    else:
        th = math.acos(d); s = math.sin(th)
        q = tuple((math.sin((1-t)*th)*x + math.sin(t*th)*y)/s for x, y in zip(a, b))
    n = math.sqrt(sum(x*x for x in q)) or 1.0
    return tuple(x/n for x in q)

def to_smd_euler(R):
    """Source RadianEuler: R = Rz(yaw) * Ry(pitch) * Rx(roll)"""
    sp = max(-1.0, min(1.0, -R[2][0])); pitch = math.asin(sp)
    if abs(sp) < 0.999999: return (math.atan2(R[2][1], R[2][2]), pitch, math.atan2(R[1][0], R[0][0]))
    return (math.atan2(-R[1][2], R[1][1]), pitch, 0.0)

def from_smd_euler(e):
    rx, ry, rz = e
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    return mmul([[cz,-sz,0],[sz,cz,0],[0,0,1]], mmul([[cy,0,sy],[0,1,0],[-sy,0,cy]], [[1,0,0],[0,cx,-sx],[0,sx,cx]]))


class Space:
    """LithTech (x right, y up, z forward, left-handed) -> Source (x forward, y left, z up, right-handed)."""
    def __init__(self, scale, mirror):
        self.scale, self.mirror = scale, mirror
        self.M = [[0, 0, 1], [1, 0, 0], [0, 1, 0]] if mirror else [[0, 0, 1], [-1, 0, 0], [0, 1, 0]]
        self.Mt = mT(self.M)
    def vec(self, v, scaled=True):
        s = self.scale if scaled else 1.0
        return tuple(c*s for c in mv(self.M, v))
    def rot(self, R): return mmul(self.M, mmul(R, self.Mt))


# ============================================================================= export
def fmt(v): return ' '.join('%.6f' % x for x in v)
def num(x): return ('%.4f' % x).rstrip('0').rstrip('.') if abs(x) > 1e-9 else '0'
def safe(n): return re.sub(r'[^A-Za-z0-9_\-]+', '_', n)

def bone_local_bind(m, i):
    def parts(n): b = m.nodes[n].bind; return [row[:3] for row in b[:3]], tuple(b[k][3] for k in range(3))
    Rw, tw = parts(i); p = m.nodes[i].parent
    if p < 0: return Rw, tw
    Rp, tp = parts(p)
    return mmul(mT(Rp), Rw), mv(mT(Rp), sub(tw, tp))

def smd_nodes(m):
    out = ['version 1', 'nodes']
    for n in m.nodes: out.append('  %d "%s" %d' % (n.index, n.name, n.parent))
    out.append('end')
    return out

def write_mesh(m, sp, path, pieces, matnames, flip_v, max_links, flip_winding):
    out = smd_nodes(m) + ['skeleton', 'time 0']
    for i in range(len(m.nodes)):
        R, t = bone_local_bind(m, i)
        out.append('  %d %s %s' % (i, fmt(sp.vec(t)), fmt(to_smd_euler(sp.rot(R)))))
    out += ['end', 'triangles']
    order = [0, 2, 1] if (sp.mirror != flip_winding) else [0, 1, 2]   # LithTech and Source both use clockwise front faces
    cw = total = maxl = 0
    for p in pieces:
        mat = matnames.get(p.material, 'mat%d' % p.material)
        for f in p.faces:
            idx = [c[2] for c in f]
            pos = [sp.vec(p.verts[k].pos) for k in idx]; nrm = [sp.vec(p.verts[k].normal, False) for k in idx]
            gn = cross(sub(pos[order[1]], pos[order[0]]), sub(pos[order[2]], pos[order[0]]))
            total += 1; cw += dot(gn, tuple(sum(c) for c in zip(*nrm))) < 0
            out.append(mat)
            for o in order:
                v = p.verts[idx[o]]
                ws = sorted(v.weights, key=lambda w: -w.bias); maxl = max(maxl, len(ws))
                if max_links and len(ws) > max_links:
                    ws = ws[:max_links]; tot = sum(w.bias for w in ws) or 1.0
                    ws = [Weight(w.node, w.loc, w.bias / tot) for w in ws]
                out.append('  %d %s %s %.6f %.6f %d %s' % (
                    ws[0].node, fmt(pos[o]), fmt(nrm[o]), f[o][0], (1.0 - f[o][1]) if flip_v else f[o][1],
                    len(ws), ' '.join('%d %.6f' % (w.node, w.bias) for w in ws)))
    out.append('end')
    with open(path, 'w') as fh: fh.write('\n'.join(out) + '\n')
    return cw, total, maxl

def sample(a, node, t_ms):
    kf, tf = a.keyframes, a.tf[node]
    if t_ms <= kf[0][0]: return tf[0]
    for k in range(len(kf) - 1):
        t0, t1 = kf[k][0], kf[k+1][0]
        if t0 <= t_ms <= t1:
            u = 0.0 if t1 == t0 else (t_ms - t0) / (t1 - t0)
            (l0, q0), (l1, q1) = tf[k], tf[k+1]
            return (tuple(x + (y - x) * u for x, y in zip(l0, l1)), slerp(q0, q1, u))
    return tf[-1]

def write_anim(m, sp, a, path, fps):
    dur = a.keyframes[-1][0]; nframes = max(1, int(round(dur * fps / 1000.0)) + 1)
    out = smd_nodes(m) + ['skeleton']
    for f in range(nframes):
        t_ms = min(dur, f * 1000.0 / fps); out.append('time %d' % f)
        for i in range(len(m.nodes)):
            loc, q = sample(a, i, t_ms)
            loc = tuple(c * LOCAL_SCALE for c in loc)
            out.append('  %d %s %s' % (i, fmt(sp.vec(loc)), fmt(to_smd_euler(sp.rot(quat_to_mat(q))))))
    out.append('end')
    with open(path, 'w') as fh: fh.write('\n'.join(out) + '\n')
    return nframes

def split_pieces(m, off, hands_first):
    body, optional, hands = [], [], {}
    for p in m.pieces:
        mm = HAND_RE.match(p.name)
        if mm: hands.setdefault(mm.group(1).lower(), []).append(p)
        elif p.name.lower() in off: optional.append(p)
        else: body.append(p)
    if len(hands) < 2:
        body += [p for v in hands.values() for p in v]; hands = {}
    order = sorted(hands, key=lambda k: (k != hands_first.lower(), k))
    return body, optional, {k: hands[k] for k in order}

ACT_GUESS = [('idle0', 'ACT_VM_IDLE'), ('deploy0', 'ACT_VM_DRAW'), ('fire', 'ACT_VM_PRIMARYATTACK'),
             ('reload0_clip0', 'ACT_VM_RELOAD'), ('dryfire', 'ACT_VM_DRYFIRE'), ('stow0', 'ACT_VM_HOLSTER')]

def write_qc(m, sp, path, args, modelname, cdmat, groups, anim_info):
    body, optional, hands = groups
    L = ['// Generated by globops_abc_batch.py -- source: %s.abc' % m.name,
         '$modelname "%s"' % modelname, '$cdmaterials "%s"' % cdmat, '']
    if not args.no_origin:
        L += ['// camera position in model space: game units %s x scale %g. -90 = studiomdl adds 90, i.e. no net rotation.'
              % (tuple(args.eye), args.scale),
              '$origin %s -90' % ' '.join(num(c) for c in sp.vec(tuple(args.eye))), '']
    L += ['$body "body" "body.smd"', '']
    for p in optional:
        L += ['$bodygroup "%s"' % safe(p.name), '{', '\tblank', '\tstudio "parts/%s.smd"' % safe(p.name), '}']
    if hands:
        L += ['$bodygroup "hands"', '{'] + ['\tstudio "parts/hands_%s.smd"' % k for k in hands] + ['}']
    L.append('')
    for s in m.sockets:
        loc = tuple(c * LOCAL_SCALE for c in s.loc)
        e = [math.degrees(x) for x in to_smd_euler(sp.rot(quat_to_mat(s.quat)))]
        L.append('$attachment "%s" "%s" %s %s %s rotate %s %s %s' % (
            (s.name, m.nodes[s.node].name) + tuple(num(c) for c in sp.vec(loc)) + tuple(num(x) for x in e)))
    L.append('')
    names = [a[0] for a in anim_info]
    act = {} if args.no_acts else {n: a for n, a in ACT_GUESS if n in names}
    for name, nframes, events in sorted(anim_info, key=lambda a: (not a[0].startswith('idle'), a[0])):
        loop = ' loop' if name.startswith(('idle', 'zoom')) else ''
        extra = ' activity "%s" 1' % act[name] if name in act else ''
        L.append('$sequence "%s" "anims/%s" fps %d%s%s' % (name, safe(name), args.fps, loop, extra))
        for fr, ev in events: L.append('//   event "%s" at frame %d' % (ev, fr))
    L += ['', '// Activities above are guesses from the animation names; check them against your weapon script.']
    with open(path, 'w') as fh: fh.write('\n'.join(L) + '\n')


# ============================================================================= per-gun driver
def prepare_dir(d, force):
    old_qc = {}
    if os.path.isdir(d) and os.listdir(d):
        if not (os.path.exists(os.path.join(d, MARKER)) or force):
            raise AbcError("output folder '%s' exists and was not created by this tool (use --force to overwrite)" % d)
        for f in os.listdir(d):
            if f.lower().endswith('.qc'): old_qc[f] = open(os.path.join(d, f), 'rb').read()
        shutil.rmtree(d)
    os.makedirs(os.path.join(d, 'anims')); os.makedirs(os.path.join(d, 'parts'))
    open(os.path.join(d, MARKER), 'w').write('written by globops_abc_batch.py\n')
    return old_qc

def mapped(args_list, key, default):
    for item in args_list:
        k, _, v = item.partition('=')
        if k.strip().lower() in (key, key[2:] if key.startswith('v_') else 'v_' + key): return v.strip()
    return default

def convert(path, outroot, args):
    m = read_abc(path)
    name = m.name
    mirror = args.mirror or any(x.strip().lower() in (name, name[2:] if name.startswith('v_') else 'v_' + name)
                                for x in args.mirror_only.split(',') if x.strip())
    sp = Space(args.scale, mirror)
    summary = '%d bones, %d pieces, %d tris, %d anims%s' % (
        len(m.nodes), len(m.pieces), sum(len(p.faces) for p in m.pieces), len(m.anims), ', MIRRORED' if mirror else '')
    off = (DEFAULT_OFF | {x.strip().lower() for x in args.off.split(',') if x.strip()}) - \
          {x.strip().lower() for x in args.on.split(',') if x.strip()}
    groups = split_pieces(m, off, args.hands)
    body, optional, hands = groups
    if args.info:
        print('%s: %s' % (name, summary))
        print('   always on : %s' % ', '.join(p.name for p in body))
        if optional: print('   hidden    : %s' % ', '.join(p.name for p in optional))
        if hands: print('   hands     : %s (default %s)' % (', '.join(hands), next(iter(hands))))
        print('   sockets   : %s' % ', '.join(s.name for s in m.sockets))
        print('   anims     : %s' % ', '.join('%s(%dms)' % (a.name, a.keyframes[-1][0]) for a in m.anims))
        return None
    gundir = os.path.join(outroot, name)
    old_qc = prepare_dir(gundir, args.force)
    matnames = {int(k): v for k, v in (s.split('=') for s in args.mat)}
    kw = dict(matnames=matnames, flip_v=not args.no_flip_v, max_links=args.max_links, flip_winding=args.flip_winding)
    stats = [write_mesh(m, sp, os.path.join(gundir, 'body.smd'), body, **kw)]
    for p in optional: stats.append(write_mesh(m, sp, os.path.join(gundir, 'parts', safe(p.name) + '.smd'), [p], **kw))
    for k, ps in hands.items(): stats.append(write_mesh(m, sp, os.path.join(gundir, 'parts', 'hands_%s.smd' % k), ps, **kw))
    cw, total, maxl = sum(x[0] for x in stats), sum(x[1] for x in stats), max(x[2] for x in stats)
    if hands and args.hands.lower() not in hands:
        print('   note: hand variant %r not found in %s (variants: %s)' % (args.hands, name, ', '.join(hands)))
    info = []
    for a in m.anims:
        n = write_anim(m, sp, a, os.path.join(gundir, 'anims', safe(a.name) + '.smd'), args.fps)
        info.append((a.name, n, [(int(round(t * args.fps / 1000.0)), s) for t, s in a.keyframes if s]))
    cdmat = mapped(args.cdmaterials_map, name, args.cdmaterials.replace('{name}', name))
    modelname = mapped(args.model, name, args.modelname.replace('{name}', name))
    write_qc(m, sp, os.path.join(gundir, name + '.qc'), args, modelname, cdmat, groups, info)
    for fn, data in old_qc.items(): open(os.path.join(gundir, fn + '.bak'), 'wb').write(data)
    return '%s  [%d/%d tris clockwise, max %d bone links%s]' % (
        summary, cw, total, maxl, ' -> capped to %d' % args.max_links if args.max_links and maxl > args.max_links else '')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Batch-convert Global Operations ABC v108 viewmodels to Source SMD + QC.')
    ap.add_argument('input', help='folder containing .abc files, or a single .abc file')
    ap.add_argument('output', nargs='?', default='abc_out', help='output folder (default ./abc_out)')
    ap.add_argument('--pattern', default='V_*.abc', help="file name filter, case-insensitive (default 'V_*.abc'; use '*.abc' for all)")
    ap.add_argument('--recursive', action='store_true', help='also search sub-folders')
    ap.add_argument('--scale', type=float, default=0.5, help='game units -> Source units (default 0.5)')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--hands', default='d', help='default hand mesh variant: b, c or d (default d)')
    ap.add_argument('--on', default='', help='comma list of pieces to show by default (e.g. handle)')
    ap.add_argument('--off', default='', help='comma list of extra pieces to hide by default')
    ap.add_argument('--mirror', action='store_true', help='mirror ALL guns left/right')
    ap.add_argument('--mirror-only', default='', metavar='NAMES', help='mirror only these guns (comma list, e.g. v_anaconda,v_m4carbine)')
    ap.add_argument('--eye', type=float, nargs=3, metavar=('RIGHT', 'UP', 'FORWARD'), default=EYE_LT,
                    help='camera position in game units for $origin (default %g %g %g)' % EYE_LT)
    ap.add_argument('--no-origin', action='store_true', help='do not write a $origin line')
    ap.add_argument('--modelname', default='weapons/{name}.mdl', help='$modelname template (default weapons/{name}.mdl)')
    ap.add_argument('--model', action='append', default=[], metavar='GUN=PATH', help='per-gun $modelname, e.g. v_anaconda=weapons/v_pist_deagle.mdl')
    ap.add_argument('--cdmaterials', default='models/weapons/v_models/{name}/', help='$cdmaterials template')
    ap.add_argument('--cdmaterials-map', action='append', default=[], metavar='GUN=PATH', help='per-gun $cdmaterials')
    ap.add_argument('--mat', action='append', default=[], metavar='N=NAME', help='material index -> name (default mat0, mat1 ...)')
    ap.add_argument('--max-links', type=int, default=3, help='max bone links per vertex (0 = keep all)')
    ap.add_argument('--no-flip-v', action='store_true')
    ap.add_argument('--flip-winding', action='store_true')
    ap.add_argument('--no-acts', action='store_true', help='do not add guessed ACT_VM_* activities to the sequences')
    ap.add_argument('--force', action='store_true', help='overwrite output folders not created by this tool')
    ap.add_argument('--info', action='store_true', help='only list the contents of each ABC')
    args = ap.parse_args(argv)

    if os.path.isfile(args.input): files = [args.input]
    else:
        files = []
        walker = os.walk(args.input) if args.recursive else [(args.input, [], os.listdir(args.input))]
        for root, _, names in walker:
            files += [os.path.join(root, n) for n in names
                      if n.lower().endswith('.abc') and fnmatch.fnmatch(n.lower(), args.pattern.lower())]
    files.sort(key=lambda p: os.path.basename(p).lower())
    if not files:
        print("No files matching '%s' found in %s" % (args.pattern, args.input)); return 1
    ok, failed, lines = 0, 0, []
    for path in files:
        base = os.path.basename(path)
        try:
            res = convert(path, args.output, args)
            if res is not None:
                ok += 1; lines.append('OK     %-28s %s' % (base, res)); print(lines[-1])
        except Exception as e:                                  # a bad file must not stop the batch
            failed += 1; lines.append('FAILED %-28s %s' % (base, e)); print(lines[-1])
    if args.info: return 0
    summary = 'Done: %d converted, %d failed, %d total.  Output: %s' % (ok, failed, len(files), os.path.abspath(args.output))
    print(summary)
    os.makedirs(args.output, exist_ok=True)
    with open(os.path.join(args.output, 'batch_report.txt'), 'w') as fh: fh.write('\n'.join(lines + [summary]) + '\n')
    return 0 if failed == 0 else 2


if __name__ == '__main__':
    sys.exit(main())
