"""Renderer GPU de TATAMI: DŌ (OpenGL 3.3 vía moderngl).

pygame sigue dibujando las capas en CPU (fondo sin suelo, luchadores, efectos y HUD).
La GPU hace lo que el CPU no puede a 60 fps:

* Suelo procedural por píxel: tatami de igusa con heri, zona roja, tarima de cedro,
  manchas de caída, luz de los shōji con la sombra de la celosía, charcos de luz de
  los farolillos y reflejos brillantes del barniz (con ondas de lluvia en el desempate).
* Sombras proyectadas de los luchadores (penumbra que crece con la distancia) y
  oclusión de contacto bajo los pies.
* Rim light 2D y sombra de forma sobre los luchadores según la dirección de la luz.
* Rayos volumétricos en espacio de pantalla que los cuerpos interrumpen.
* HDR: bloom de doble filtro (Karis + tienda), tonemapping AgX, gradación por asalto,
  aberración cromática, ondas de choque, foco cenital, frame de impacto, viñeta,
  grano de película, profundidad de campo por CoC, LUTs del Modo Foto,
  filtros de accesibilidad y FXAA.
"""
from __future__ import annotations

import math
from typing import List, Optional

import pygame

try:
    import moderngl
    import numpy as np
except Exception:  # pragma: no cover
    moderngl = None
    np = None

from .config import FLOOR_Y, H, W, cam_base_off, mixc

LUT_IDS = {"Natural": 0, "Caravaggio": 1, "Hammershøi": 2, "Kurosawa B/N": 3, "Daido Moriyama": 4}
DALT_IDS = {"off": 0, "protanopia": 1, "deuteranopia": 2, "tritanopia": 3}

# ─────────────────────────────────────────────────────────────────────────────
#  SHADERS
# ─────────────────────────────────────────────────────────────────────────────
VERT = """
#version 330
in vec2 in_pos;
out vec2 uv;
void main() { uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

COMMON = """
#version 330
in vec2 uv;
out vec4 frag;
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * .1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
    return mix(mix(hash12(i), hash12(i + vec2(1, 0)), f.x), mix(hash12(i + vec2(0, 1)), hash12(i + vec2(1, 1)), f.x), f.y);
}
float fbm(vec2 p) { float a = .5, s = 0.; for (int i = 0; i < 4; i++) { s += a * vnoise(p); p = p * 2.03 + 17.1; a *= .5; } return s; }
// franja filtrada: se funde a gris cuando es más fina que un píxel (sin aliasing)
float fstripe(float x, float period) {
    float w = fwidth(x) / period;
    return mix(0.5 + 0.5 * cos(6.2831853 * x / period), 0.5, clamp(w * 1.3, 0., 1.));
}
vec3 toLin(vec3 c) { return pow(max(c, 0.), vec3(2.2)); }
float luma(vec3 c) { return dot(c, vec3(.2126, .7152, .0722)); }
"""

SCENE = COMMON + """
uniform sampler2D tBg, tFig, tFx, tFigD;
uniform vec2 uRes;
uniform float uTime;
uniform vec3 uKey3, uSky, uBounce;
uniform float uFigLit;
uniform float uTexI;          // intensidad de las texturas procedurales (0 = lisas)
uniform vec4 uCam;            // cam.x, zoom, base_y (px), shake_x
uniform float uDepthScale;    // px por unidad de profundidad
uniform float uFloorBackY;
uniform vec3 uKey, uAmb, uLan, uTint, uBeamCol;
uniform float uBeamI, uLanI, uRefl, uWet, uRimI, uEmissive, uSkyFill;
uniform vec2 uLightDir;       // hacia la luz, en px de pantalla (y hacia abajo)
uniform vec4 uShadow;         // kx, ky, suavidad, intensidad
uniform vec4 uLanterns[4];    // sx, sy, x mundo, intensidad
uniform vec4 uFighters[2];    // sx, pies y, altura en el aire (px), ancho (px)
uniform vec4 uDecals[16];     // x mundo, escala, opacidad, z mundo
uniform int uDecalN;

const float ZS = 571.43;      // cm por unidad de profundidad

vec4 Lp(sampler2D t, vec2 p) { return texture(t, p / uRes); }
vec4 Lp(sampler2D t, vec2 p, float lod) { return textureLod(t, p / uRes, lod); }

vec3 tatami(float X, float Z, vec3 dye, out float gloss) {
    float row = floor((Z + 285.5) / 100.);
    float off = mod(row, 2.) * 100.;
    float lx = mod(X + 400. + off, 200.);
    float lz = mod(Z + 285.5, 100.);
    float col = floor((X + 400. + off) / 200.);
    float id = hash12(vec2(col, row) + 3.7);
    float rush = fstripe(Z, 0.55);
    float stalk = hash12(vec2(floor(Z / 0.55), col * 7. + row));
    stalk = mix(stalk, 0.5, clamp(fwidth(Z) / 0.55 * 1.3, 0., 1.));
    float warp = fstripe(X, 1.7);
    vec3 c = dye * (0.84 + 0.16 * id) * (0.9 + 0.12 * rush + 0.1 * (stalk - 0.5)) * (0.94 + 0.06 * warp);
    float wear = exp(-(X * X + (Z - 115.) * (Z - 115.)) / (260. * 260.));
    c = mix(c, c * vec3(1.1, 1.06, 0.88), wear * 0.55);
    c *= 0.88 + 0.24 * fbm(vec2(X, Z) * 0.018 + id * 13.);
    float heri = 1. - step(3.5, lz) * step(lz, 96.5);
    vec3 heriC = vec3(0.02, 0.025, 0.05) * (0.75 + 0.5 * fstripe(X, 0.9));
    c = mix(c, heriC, heri);
    float seam = 1. - smoothstep(0., 0.8 + 2. * fwidth(lx), min(lx, 200. - lx));
    c *= 1. - 0.55 * seam;
    // brillo del tejido: depende de la orientación de la estera (las vecinas se ven más claras u oscuras)
    gloss = mix(0.45 + 0.35 * id, 0.15, heri);
    c *= 0.95 + 0.1 * mod(row + col, 2.);
    return c;
}

vec3 wood(float X, float Z, out float gloss) {
    float pz = Z / 18.;
    float plank = floor(pz);
    float lz = fract(pz);
    float pid = hash12(vec2(plank, 3.1));
    float px = X / 240. + pid * 7.;
    float seg = floor(px);
    float grain = fbm(vec2(X * 0.012 + pid * 20., Z * 0.5));
    float rings = fstripe(X + grain * 45., 8.);
    vec3 c = vec3(0.26, 0.13, 0.06) * (0.7 + 0.4 * hash12(vec2(plank, seg))) * (0.82 + 0.3 * grain) * (0.92 + 0.08 * rings);
    float gap = 1. - smoothstep(0., 0.03 + fwidth(pz) * 1.5, min(lz, 1. - lz));
    float butt = 1. - smoothstep(0., 0.003 + fwidth(px), min(fract(px), 1. - fract(px)));
    c *= 1. - 0.65 * max(gap, butt);
    gloss = 0.95;
    return c;
}

// luz del atardecer que atraviesa los shōji y dibuja la celosía (kumiko) sobre el suelo
float windowLight(float X, float Z, float k) {
    float zt = 520. - Z;
    // espacio lineal en pantalla (X·k): así las diagonales de luz no se curvan con la perspectiva
    float s = X * k - zt * 0.36;
    float P = 132.;
    float u = mod(s + 100. * P, P) / P;
    float blur = 0.01 + zt * 0.00004;
    float win = smoothstep(0.04, 0.04 + blur * 4., u) * (1. - smoothstep(0.88 - blur * 4., 0.88, u));
    float vb = 0.;
    for (int k = 1; k < 4; k++) vb = max(vb, 1. - smoothstep(0.006, 0.006 + blur * 2., abs(u - 0.04 - 0.21 * float(k))));
    float hd = abs(mod(zt + 55., 110.) - 55.);
    float hb = 1. - smoothstep(2.5, 2.5 + zt * 0.012, hd);
    float reach = 1. - smoothstep(520., 860., zt);
    float toko = 1. - 0.88 * (1. - smoothstep(90., 140., abs(s + 60.)));
    return win * (1. - 0.7 * vb) * (1. - 0.55 * hb) * reach * toko;
}

float figShadow(vec2 p, float yf) {
    float dy = p.y - yf;
    if (dy <= 0.) return 0.;
    float h = dy / uShadow.y;
    vec2 src = vec2(p.x - h * uShadow.x, yf - h);
    if (src.y < 1.) return 0.;
    float lod = 0.6 + h * uShadow.z;
    return Lp(tFig, src, lod).a * uShadow.w * (1. - smoothstep(60., 560., h));
}

vec3 lanternLight(float X, float Z, out vec3 spec, vec2 p) {
    vec3 acc = vec3(0);
    spec = vec3(0);
    for (int i = 0; i < 4; i++) {
        vec4 l = uLanterns[i];
        if (l.w <= 0.) continue;
        vec3 d = vec3(X - l.z, -230., Z - 470.);
        float r2 = dot(d, d);
        float ndl = clamp(230. / sqrt(r2), 0., 1.);
        acc += uLan * l.w * ndl * 70000. / (r2 + 6000.);
        // reflejo especular alargado del farolillo en el suelo barnizado
        float yb = uCam.z - 0.9 * uDepthScale;
        float ry = yb + (yb - l.y) * 0.55;
        vec2 q = vec2((p.x - l.x) / 7., (p.y - ry) / (26. + 40. * uWet));
        spec += uLan * l.w * exp(-dot(q, q)) * 2.2;
    }
    return acc;
}

vec3 shadeFloor(vec2 p, out float coc) {
    float d = (uCam.z - p.y) / uDepthScale;
    float k = 1. - 0.42 * d;
    float X = (p.x - uRes.x * 0.5 - uCam.w) / (uCam.y * 0.95 * k) + uCam.x;
    float Z = d * ZS;
    coc = clamp(abs(d) * 1.1, 0., 1.);
    float gloss;
    vec3 alb;
    bool tat = abs(X) <= 400. && Z >= -285.5 && Z <= 514.;
    bool safe = abs(X) <= 520. && Z >= -342.6 && Z <= 543.;
    if (tat) alb = tatami(X, Z, vec3(0.50, 0.41, 0.2), gloss);
    else if (safe) alb = tatami(X + 17., Z + 31., vec3(0.36, 0.045, 0.035), gloss);
    else alb = wood(X, Z, gloss);
    // marcas de caída
    float dn = fbm(vec2(X, Z) * 0.09);
    for (int i = 0; i < 16; i++) {
        if (i >= uDecalN) break;
        vec4 dc = uDecals[i];
        vec2 q = vec2((X - dc.x) / (58. * dc.y), (Z - dc.w) / (16. * dc.y));
        float m = exp(-dot(q, q) * 1.5) * (0.55 + 0.45 * dn);
        alb *= 1. - 0.32 * dc.z * m;
        gloss *= 1. - 0.6 * m;
    }
    float yf = uCam.z;
    float ao = 1.;
    for (int i = 0; i < 2; i++) {
        vec4 f = uFighters[i];
        vec2 q = vec2((p.x - f.x) / max(f.w, 1.), (p.y - f.y) / max(f.w * 0.2, 1.));
        float air = clamp(f.z / 180., 0., 1.);
        ao *= 1. - 0.65 * (1. - air) * exp(-dot(q, q) * 2.0);
    }
    float sh = 1. - figShadow(p, yf);
    vec3 lspec;
    vec3 lan = lanternLight(X, Z, lspec, p);
    vec3 light = uAmb * ao + uKey * (uBeamI * windowLight(X, Z, k) + uSkyFill) * sh * ao + lan * mix(1., sh, 0.5) * ao;
    vec3 col = alb * light;
    // reflejos: luchadores, pared y farolillos
    float refl = uRefl * gloss * (1. + uWet);
    vec2 rip = vec2(0.);
    if (uWet > 0.) {
        vec2 cell = floor(p / vec2(64., 20.));
        vec2 cpos = (cell + vec2(hash12(cell), hash12(cell + 7.))) * vec2(64., 20.);
        float t = fract(uTime * 0.9 + hash12(cell + 3.));
        vec2 q = (p - cpos) * vec2(1., 2.8);
        float r = length(q);
        float ring = sin(r * 0.7 - t * 22.) * exp(-r * 0.07) * (1. - t) * step(r, t * 48.);
        rip = normalize(q + 1e-3) * ring * 4.;
    }
    float dy = p.y - yf;
    if (dy > 0.) {
        vec4 rf = Lp(tFig, vec2(p.x, yf - dy) + rip, 1. + dy * 0.03 * (1. - 0.5 * uWet));
        col += toLin(rf.rgb) * uTint * rf.a * refl * exp(-dy * 0.009);
    }
    float dw = p.y - uFloorBackY;
    if (dw > 0.) {
        vec4 wb = Lp(tBg, vec2(p.x, uFloorBackY - dw * 1.4) + rip, 1.5 + dw * 0.025);
        vec3 wl = toLin(wb.rgb);
        col += wl * (1. + uEmissive * smoothstep(0.3, 0.8, luma(wl))) * wb.a * refl * 0.7 * exp(-dw * 0.011);
    }
    col += lspec * gloss * uLanI * (0.25 + uWet);
    return col;
}

// ── luz por píxel de los luchadores ──────────────────────────────────────────
// tFigD: R altura (grupos de profundidad + cúpula por pieza), G material, B brillo.
// 0 tinta · 1 piel · 2 algodón · 3 satén/lycra · 4 cuero · 5 pelo · 6 ojos
float figH(vec2 q) { return Lp(tFigD, q, 1.0).r * 0.5 + Lp(tFigD, q, 2.3).r * 0.5; }

// ── texturas procedurales por material ──────────────────────────────────────
// Coordenadas en cm, ancladas a la raíz del luchador más cercano (no resbalan al moverse por el
// tatami). Cada patrón devuelve una altura h: modula el albedo y, derivada en pantalla, inclina la
// normal para que la luz rasante saque el grano. Todo se funde a su media cuando el detalle es
// más fino que un píxel (sin moiré).
float aaFade(vec2 q, float period) { float w = max(fwidth(q.x), fwidth(q.y)) / period; return 1. - clamp(w * 1.6 - 0.4, 0., 1.); }

float matTex(vec2 p, int mat, out float albK, out float specK) {
    float s = uCam.y * 0.95;
    vec4 f = abs(p.x - uFighters[0].x) < abs(p.x - uFighters[1].x) ? uFighters[0] : uFighters[1];
    vec2 q = (p - vec2(f.x, f.y - f.z)) / s;
    albK = 1.; specK = 1.;
    float h = 0.;
    if (mat == 2) {                                  // algodón: trama de grano de arroz + hilos gruesos
        float a = aaFade(q, 0.5);
        vec2 c = q / vec2(0.42, 0.34);
        vec2 cell = floor(c);
        vec2 fc = fract(c + vec2(mod(cell.y, 2.) * 0.5, 0.)) - 0.5;
        float grain = 1. - smoothstep(0.18, 0.5, length(fc * vec2(1.0, 1.7)));
        float slub = vnoise(q * vec2(0.35, 2.6)) * vnoise(q * vec2(1.7, 0.25) + 9.);
        h = (grain - 0.5) * a + (slub - 0.25) * 0.9;
        albK = 1. + 0.07 * (grain - 0.5) * a + 0.1 * (slub - 0.25) - 0.04 * fbm(q * 0.18);
    } else if (mat == 1) {                           // piel: moteado cálido + poros
        float a = aaFade(q, 0.35);
        float mott = fbm(q * 0.28);
        float pore = vnoise(q * 3.1);
        h = (pore - 0.5) * 0.35 * a + (mott - 0.5) * 0.3;
        albK = 1. + 0.08 * (mott - 0.5) - 0.03 * smoothstep(0.62, 0.8, pore) * a;
        specK = 0.75 + 0.5 * pore;
    } else if (mat == 3) {                           // satén y lycra: vetas anisótropas verticales
        float a = aaFade(q, 0.3);
        float st = vnoise(vec2(q.x * 2.2, q.y * 0.1)) * 0.7 + vnoise(vec2(q.x * 7.0, q.y * 0.25)) * 0.3 * a;
        h = (st - 0.5) * 0.25;
        albK = 0.98 + 0.04 * st;
        specK = 0.6 + 0.8 * st;
    } else if (mat == 4) {                           // cuero: granulado y craquelado
        float a = aaFade(q, 0.4);
        float g1 = vnoise(q * 1.6);
        float cr = (1. - smoothstep(0.015, 0.06, abs(fbm(q * 0.6) - 0.5))) * a;
        h = (g1 - 0.5) * 0.25 * a - cr * 0.3;
        albK = 1. - 0.1 * cr - 0.04 * (g1 - 0.5) * a;
        specK = 0.85 + 0.3 * g1 - 0.4 * cr;
    } else if (mat == 5) {                           // pelo: hebras finas a lo largo de la caída
        float a = aaFade(q, 0.25);
        float sx = q.x + 0.6 * vnoise(q * vec2(0.2, 0.5));
        float st = vnoise(vec2(sx * 4.5, q.y * 0.3)) * 0.65 + vnoise(vec2(sx * 12., q.y * 0.7)) * 0.35 * a;
        h = (st - 0.5) * 0.6;
        albK = 0.9 + 0.2 * st;
        specK = 0.4 + 1.2 * st * st;
    }
    return h;
}

vec3 litFighter(vec2 p, vec4 fg) {
    vec3 alb = toLin(fg.rgb);
    vec4 fd = Lp(tFigD, p);
    int mat = int(fd.g * 255. / 36. + 0.5);
    if (mat == 0) return alb * uTint;                       // tinta: el contorno no se ilumina
    float e = 2.0;
    float hx = figH(p + vec2(e, 0.)) - figH(p - vec2(e, 0.));
    float hy = figH(p + vec2(0., e)) - figH(p - vec2(0., e));
    float bump = mat == 5 ? 5.0 : 7.0;
    float albK, specK;
    float th = matTex(p, mat, albK, specK) * uTexI;
    alb *= mix(1., albK, uTexI);
    vec2 tg = vec2(dFdx(th), -dFdy(th));                   // relieve del grano, en espacio de pantalla
    float tb = mat == 2 ? 0.55 : mat == 4 ? 0.3 : mat == 5 ? 0.45 : 0.3;
    vec3 N = normalize(vec3(-hx * bump - tg.x * tb, hy * bump - tg.y * tb, 1.0));
    float ao = 1. - clamp((Lp(tFigD, p, 4.2).r - fd.r) * 2.2, 0., 0.5);
    vec3 V = vec3(0., 0., 1.);
    vec3 L = normalize(uKey3);
    vec3 Hv = normalize(L + V);
    float spec = fd.b * mix(1., specK, uTexI);
    float ndl = dot(N, L);
    // ── rampa de dos tonos (anime 2026): borde definido y suave, sombra de color desplazado ──
    float soft = mat == 1 ? 0.16 : 0.1;
    float lit = smoothstep(-0.08 - soft, -0.08 + soft, ndl) * mix(1.0, ao, 0.6);
    vec3 amb = mix(uBounce, uSky, N.y * 0.5 + 0.5) * ao;
    vec3 shTint = mat == 1 ? vec3(1.06, 0.74, 0.68) : mat == 5 ? vec3(0.9, 0.86, 1.05) : vec3(0.84, 0.9, 1.08);
    vec3 shadowCol = alb * (amb * 1.15 + uKey * 0.1) * shTint;
    vec3 litCol = alb * (amb + uKey * (0.82 + 0.18 * max(ndl, 0.)));
    vec3 col = mix(shadowCol, litCol, lit);
    if (mat == 1) col += alb * uKey * vec3(1.0, 0.3, 0.16) * 1.2 * lit * (1. - lit);   // dispersión en el borde de sombra
    // farolillos: luces puntuales con la misma rampa
    vec3 P3 = vec3(p.x, -p.y, 0.);
    float lanHi = 0.;
    for (int i = 0; i < 4; i++) {
        vec4 l = uLanterns[i];
        if (l.w <= 0.) continue;
        vec3 d = vec3(l.x, -l.y, 140.) - P3;
        float r = length(d);
        vec3 Ld = d / r;
        float att = l.w * 160000. / (r * r + 40000.);
        float ramp = smoothstep(-0.12, 0.12, dot(N, Ld));
        col += alb * uLan * att * ramp * 0.6;
        lanHi += att * smoothstep(0.94, 0.975, dot(N, normalize(Ld + V)));
    }
    // brillos duros por material
    float nh = dot(N, Hv);
    float hi = 0.;
    if (mat == 1) hi = smoothstep(0.955, 0.985, nh) * (0.08 + 0.85 * spec);                       // sudor
    else if (mat == 3) hi = smoothstep(0.8, 0.9, nh) * 0.45 + pow(1. - N.z, 3.) * 0.15;          // satén y lycra
    else if (mat == 4) hi = smoothstep(0.9, 0.97, nh) * 0.7 * spec;                                     // cuero
    else if (mat == 5) hi = smoothstep(0.86, 0.93, dot(normalize(N + vec3(0., 0.55, 0.)), Hv)) * (0.3 + 0.4 * spec);   // anillo del pelo
    else if (mat == 2) hi = smoothstep(0.62, 0.72, 1. - N.z) * 0.12;                             // fibra del algodón
    col += (uKey * lit + uLan * uLanI * 0.4) * hi + uLan * lanHi * (mat == 1 || mat == 4 ? 0.6 : 0.15);
    // contraluz recortado: shōji del lado de la luz, farolillos desde arriba
    float rimM = smoothstep(0.5, 0.62, 1. - N.z);
    col += uKey * uRimI * 0.55 * rimM * smoothstep(0.1, 0.5, dot(normalize(N.xy + 1e-4), normalize(L.xy + 1e-4)));
    col += uLan * uLanI * 0.6 * rimM * smoothstep(0.2, 0.7, N.y);
    return col * uTint;
}

void main() {
    vec2 p = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y);
    vec4 bg = Lp(tBg, p);
    float coc = 1.;
    vec3 col = vec3(0.);
    if (bg.a < 0.999) col = shadeFloor(p, coc);
    vec3 b = toLin(bg.rgb) * uTint;
    if (p.y < uFloorBackY) b *= 1. + uEmissive * smoothstep(0.3, 0.8, luma(b)) * 2.2;
    col = mix(col, b, bg.a);
    coc = mix(coc, p.y < uFloorBackY ? 1. : 0.75, bg.a);

    vec4 fg = Lp(tFig, p);
    if (fg.a > 0.004 && uFigLit > 0.5) {
        col = mix(col, litFighter(p, fg), fg.a);
        coc = mix(coc, 0., fg.a);
    } else if (fg.a > 0.004) {
        vec3 c = toLin(fg.rgb) * uTint;
        vec2 ld = normalize(uLightDir);
        float aL = Lp(tFig, p + ld * 3.5).a;
        float aS = Lp(tFig, p - ld * 5.).a;
        c *= 1. - 0.32 * (1. - aS) * fg.a;
        c += fg.a * (1. - aL) * uKey * uRimI;
        float aU = Lp(tFig, p + vec2(0., -3.)).a;
        c += fg.a * (1. - aU) * uLan * uLanI * 0.9;
        col = mix(col, c, fg.a);
        coc = mix(coc, 0., fg.a);
    }
    vec4 fx = Lp(tFx, p);
    vec3 fc = toLin(fx.rgb);
    float lf = luma(fc);
    col = mix(col, fc * (1. + 3. * lf * lf), fx.a);
    frag = vec4(col, coc);
}
"""

RAYS = COMMON + """
uniform sampler2D tBg, tFig;
uniform vec2 uRes;
uniform vec2 uLightUV;
uniform float uFloorBackY, uDensity, uDecay, uTime;
float mask(vec2 q) {
    vec4 b = textureLod(tBg, q, 3.5);     // máscara desenfocada: sin moiré con la celosía
    float a = textureLod(tFig, q, 2.0).a;
    float wall = step(q.y * uRes.y, uFloorBackY);
    return b.a * wall * smoothstep(0.5, 0.92, dot(b.rgb, vec3(.299, .587, .114))) * (1. - a);
}
void main() {
    vec2 q = vec2(uv.x, 1. - uv.y);
    vec2 delta = (q - uLightUV) * (uDensity / 96.);
    float illum = 1., acc = 0.;
    vec2 s = q;
    s -= delta * hash12(gl_FragCoord.xy + fract(uTime) * 61.);
    for (int i = 0; i < 96; i++) { s -= delta; acc += mask(s) * illum; illum *= uDecay; }
    // los cuerpos están delante del aire iluminado: no se lavan con la neblina
    acc *= 1. - 0.9 * texture(tFig, q).a;
    frag = vec4(vec3(acc / 96. * 3.), 1.);
}
"""

DOWN = COMMON + """
uniform sampler2D tSrc;
uniform vec2 uTexel;
uniform int uKaris;
vec3 s(vec2 o) { return texture(tSrc, uv + o * uTexel).rgb; }
float kw(vec3 c) { return 1. / (1. + luma(c)); }
void main() {
    vec3 a = s(vec2(-2, 2)), b = s(vec2(0, 2)), c = s(vec2(2, 2)), d = s(vec2(-2, 0)), e = s(vec2(0, 0)), f = s(vec2(2, 0));
    vec3 g = s(vec2(-2, -2)), h = s(vec2(0, -2)), i = s(vec2(2, -2)), j = s(vec2(-1, 1)), k = s(vec2(1, 1)), l = s(vec2(-1, -1)), m = s(vec2(1, -1));
    vec3 o;
    if (uKaris == 1) {
        vec3 g0 = (a + b + d + e) * .25, g1 = (b + c + e + f) * .25, g2 = (d + e + g + h) * .25, g3 = (e + f + h + i) * .25, g4 = (j + k + l + m) * .25;
        float w0 = kw(g0) * .125, w1 = kw(g1) * .125, w2 = kw(g2) * .125, w3 = kw(g3) * .125, w4 = kw(g4) * .5;
        o = (g0 * w0 + g1 * w1 + g2 * w2 + g3 * w3 + g4 * w4) / (w0 + w1 + w2 + w3 + w4);
    } else {
        o = e * .125 + (a + c + g + i) * .03125 + (b + d + f + h) * .0625 + (j + k + l + m) * .125;
    }
    frag = vec4(max(o, 0.), 1.);
}
"""

UP = COMMON + """
uniform sampler2D tSrc;
uniform vec2 uTexel;
void main() {
    vec2 r = uTexel;
    vec3 o = texture(tSrc, uv + vec2(-r.x, r.y)).rgb + 2. * texture(tSrc, uv + vec2(0, r.y)).rgb + texture(tSrc, uv + r).rgb
           + 2. * texture(tSrc, uv + vec2(-r.x, 0)).rgb + 4. * texture(tSrc, uv).rgb + 2. * texture(tSrc, uv + vec2(r.x, 0)).rgb
           + texture(tSrc, uv - r).rgb + 2. * texture(tSrc, uv + vec2(0, -r.y)).rgb + texture(tSrc, uv + vec2(r.x, -r.y)).rgb;
    frag = vec4(o / 16., 1.);
}
"""

DOF = COMMON + """
uniform sampler2D tScene;
uniform vec2 uTexel;
uniform float uDof;
void main() {
    vec4 c = texture(tScene, uv);
    float coc = c.a * uDof;
    if (coc < 0.6) { frag = c; return; }
    vec3 acc = c.rgb;
    float wsum = 1.;
    for (int i = 0; i < 32; i++) {
        float r = sqrt((float(i) + 0.5) / 32.);
        float th = float(i) * 2.39996;
        vec2 o = vec2(cos(th), sin(th)) * r * coc;
        vec4 s = texture(tScene, uv + o * uTexel);
        float w = clamp(s.a * uDof / (length(o) + 0.5), 0., 1.) * (1. + 1.5 * luma(s.rgb));
        acc += s.rgb * w;
        wsum += w;
    }
    frag = vec4(acc / wsum, c.a);
}
"""

FINAL = COMMON + """
uniform sampler2D tScene, tBloom, tRays;
uniform vec2 uRes;
uniform float uTime, uBloom, uExposure, uRaysI, uCA, uTilt, uVig, uGrain, uSpot, uImpact, uDim, uSat, uPower;
uniform vec3 uRaysCol, uLift, uGain;
uniform vec4 uWaves[4];
uniform vec2 uSpotPos;
uniform int uLut, uDalt, uHiContrast;

vec3 agxContrast(vec3 x) {
    vec3 x2 = x * x, x4 = x2 * x2;
    return 15.5 * x4 * x2 - 40.14 * x4 * x + 31.96 * x4 - 6.868 * x2 * x + 0.4298 * x2 + 0.1191 * x - 0.00232;
}
vec3 agx(vec3 v) {
    const mat3 m = mat3(0.842479062253094, 0.0423282422610123, 0.0423756549057051,
                        0.0784335999999992, 0.878468636469772, 0.0784336,
                        0.0792237451477643, 0.0791661274605434, 0.879142973793104);
    const float lo = -12.47393, hi = 4.026069;
    v = m * v;
    v = clamp(log2(max(v, 1e-10)), lo, hi);
    v = (v - lo) / (hi - lo);
    return agxContrast(v);
}
vec3 agxEotf(vec3 v) {
    const mat3 mi = mat3(1.19687900512017, -0.0528968517574562, -0.0529716355144438,
                         -0.0980208811401368, 1.15190312990417, -0.0980434501171241,
                         -0.0990297440797205, -0.0989611768448433, 1.15107367264116);
    return pow(max(mi * v, 0.), vec3(2.2));
}
vec3 daltonize(vec3 c, int mode) {
    vec3 lms = vec3(17.8824 * c.r + 43.5161 * c.g + 4.11935 * c.b,
                    3.45565 * c.r + 27.1554 * c.g + 3.86714 * c.b,
                    0.0299566 * c.r + 0.184309 * c.g + 1.46709 * c.b);
    vec3 s = lms;
    if (mode == 1) s.x = 2.02344 * lms.y - 2.52581 * lms.z;
    else if (mode == 2) s.y = 0.494207 * lms.x + 1.24827 * lms.z;
    else s.z = -0.395913 * lms.x + 0.801109 * lms.y;
    vec3 sim = vec3(0.0809444479 * s.x - 0.130504409 * s.y + 0.116721066 * s.z,
                    -0.0102485335 * s.x + 0.0540193266 * s.y - 0.113614708 * s.z,
                    -0.000365296938 * s.x - 0.00412161469 * s.y + 0.693511405 * s.z);
    vec3 e = c - sim;
    return clamp(c + vec3(0., 0.7 * e.r + e.g, 0.7 * e.r + e.b), 0., 1.);
}
void main() {
    vec2 q = uv;
    if (abs(uTilt) > 0.01) {
        float asp = uRes.x / uRes.y, a = radians(uTilt);
        vec2 c0 = (q - 0.5) * vec2(asp, 1.);
        c0 = mat2(cos(a), -sin(a), sin(a), cos(a)) * c0 / 1.05;
        q = c0 / vec2(asp, 1.) + 0.5;
    }
    vec2 p = vec2(q.x * uRes.x, (1. - q.y) * uRes.y);
    vec2 off = vec2(0.);
    for (int i = 0; i < 4; i++) {
        vec4 w = uWaves[i];
        if (w.w <= 0.) continue;
        vec2 dv = p - w.xy;
        dv.y *= 1.5;
        float r = length(dv);
        off += normalize(dv + 1e-4) * exp(-pow((r - w.z) / 16., 2.)) * w.w * 12.;
    }
    p -= off;
    q = vec2(p.x / uRes.x, 1. - p.y / uRes.y);
    vec2 dir = q - 0.5;
    float ca = uCA * (0.0015 + 0.012 * dot(dir, dir));
    vec3 col = vec3(texture(tScene, q + dir * ca).r, texture(tScene, q).g, texture(tScene, q - dir * ca).b);
    col += texture(tRays, q).rgb * uRaysCol * uRaysI;
    col = mix(col, texture(tBloom, q).rgb, uBloom);
    if (uSpot > 0.) {
        float hw = 60. + clamp(p.y / uSpotPos.y, 0., 1.3) * 250.;
        float m = 1. - smoothstep(hw * 0.7, hw * 1.15, abs(p.x - uSpotPos.x));
        col *= mix(1., 0.16 + 1.6 * m, uSpot);
    }
    col *= exp2(uExposure);
    vec3 a = agx(col);
    float l = luma(a);
    a = pow(max(a, 0.), vec3(uPower));
    a = l + uSat * (a - l);
    col = agxEotf(a);
    col = col * uGain + uLift * (1. - col);
    // LUTs del Modo Foto
    float lc = luma(col);
    if (uLut == 1) {            // Caravaggio: tenebrismo cálido
        col = mix(vec3(lc), col, 1.15) * vec3(1.08, 0.97, 0.82);
        col = pow(col, vec3(1.35)) * 1.25;
    } else if (uLut == 2) {     // Hammershøi: gris silencioso
        col = mix(vec3(lc), col, 0.25) * vec3(0.92, 0.96, 1.0) + 0.025;
    } else if (uLut == 3) {     // Kurosawa B/N
        float m = smoothstep(0.0, 1.0, pow(lc, 0.85));
        col = vec3(m * m * (3. - 2. * m));
    } else if (uLut == 4) {     // Moriyama: blanco y negro quemado
        float m = clamp((pow(lc, 0.7) - 0.35) * 2.6 + 0.35, 0., 1.);
        col = vec3(m);
    }
    vec2 vq = (uv - 0.5) * vec2(uRes.x / uRes.y, 1.);
    float vig = uVig + (uLut == 1 ? 0.45 : uLut == 4 ? 0.3 : 0.);
    col *= 1. - vig * smoothstep(0.3, 1.1, length(vq));
    vec3 s = pow(max(col, 0.), vec3(1. / 2.2));
    if (uImpact > 0.) {
        float m = step(0.42, luma(s));
        s = mix(s, mix(vec3(0.97, 0.94, 0.88), vec3(0.06, 0.03, 0.04), m), uImpact);
    }
    s *= 1. - uDim;
    if (uHiContrast == 1) s = clamp((s - 0.5) * 1.35 + 0.5, 0., 1.);
    if (uDalt > 0) s = daltonize(s, uDalt);
    float gr = uGrain * (uLut == 4 ? 3.5 : 1.);
    float n = hash12(gl_FragCoord.xy + fract(uTime * 7.31) * vec2(91.7, 37.3)) - 0.5;
    s += n * gr * (0.45 + 0.55 * (1. - luma(s)));
    s += (hash12(gl_FragCoord.xy * 1.37 + uTime) - 0.5) / 255.;
    frag = vec4(clamp(s, 0., 1.), 1.);
}
"""

PRESENT = COMMON + """
uniform sampler2D tLdr, tHud;
uniform vec2 uTexel;
uniform int uFxaa;
vec3 fxaa(vec2 p) {
    vec3 nw = texture(tLdr, p + vec2(-1., -1.) * uTexel).rgb, ne = texture(tLdr, p + vec2(1., -1.) * uTexel).rgb;
    vec3 sw = texture(tLdr, p + vec2(-1., 1.) * uTexel).rgb, se = texture(tLdr, p + vec2(1., 1.) * uTexel).rgb;
    vec3 m = texture(tLdr, p).rgb;
    vec3 L = vec3(0.299, 0.587, 0.114);
    float lnw = dot(nw, L), lne = dot(ne, L), lsw = dot(sw, L), lse = dot(se, L), lm = dot(m, L);
    float lmin = min(lm, min(min(lnw, lne), min(lsw, lse))), lmax = max(lm, max(max(lnw, lne), max(lsw, lse)));
    vec2 d = vec2(-((lnw + lne) - (lsw + lse)), ((lnw + lsw) - (lne + lse)));
    float red = max((lnw + lne + lsw + lse) * (0.25 / 8.), 1. / 128.);
    float rcp = 1. / (min(abs(d.x), abs(d.y)) + red);
    d = clamp(d * rcp, -8., 8.) * uTexel;
    vec3 a = 0.5 * (texture(tLdr, p + d * (1. / 3. - 0.5)).rgb + texture(tLdr, p + d * (2. / 3. - 0.5)).rgb);
    vec3 b = a * 0.5 + 0.25 * (texture(tLdr, p - d * 0.5).rgb + texture(tLdr, p + d * 0.5).rgb);
    float lb = dot(b, L);
    return (lb < lmin || lb > lmax) ? a : b;
}
void main() {
    vec3 c = uFxaa == 1 ? fxaa(uv) : texture(tLdr, uv).rgb;
    vec4 h = texture(tHud, vec2(uv.x, 1. - uv.y));
    frag = vec4(mix(c, h.rgb, h.a), 1.);   // pygame guarda alfa directo
}
"""


# ─────────────────────────────────────────────────────────────────────────────
#  PARÁMETROS DE ESCENA (CPU → GPU)
# ─────────────────────────────────────────────────────────────────────────────
def _lin(c, k=1.0):
    return tuple(((v / 255.0) ** 2.2) * k for v in c)


def scene_params(dojo, cam, fighters, fx, cfg, extra=None) -> dict:
    """Recoge todo lo que la GPU necesita de un fotograma: cámara, luz, luchadores, decals, ondas."""
    rig = dojo.rig
    g = dojo.gpu_rig
    zoom = cam.zoom
    base_y = FLOOR_Y + cam.yoff + cam.shake_y + cam_base_off(zoom)
    depth = 190 * (0.75 + zoom * 0.25)
    lanterns = []
    for (sx, sy, inten) in getattr(dojo, "lantern_pos", [])[:4]:
        k = 1 - 0.42 * 0.9
        wx = (sx - W / 2 - cam.shake_x) / (zoom * 0.95 * k) + cam.x
        lanterns.append((sx, sy, wx, inten))
    while len(lanterns) < 4:
        lanterns.append((0.0, 0.0, 0.0, 0.0))
    figs = []
    for f in fighters[:2]:
        sx, _ = cam.P(f.x, 0)
        figs.append((sx, base_y, max(0.0, -f.y) * zoom * 0.95, cam.S(46 * f.width * f.scale)))
    while len(figs) < 2:
        figs.append((-9999.0, base_y, 0.0, 1.0))
    decals = [(x, s, 1.0, dd * 2.0) for (x, dd, s) in dojo.decals[-16:]]
    waves = []
    if fx is not None:
        for wv in fx.waves[:4]:
            sx, sy = cam.P(wv[0], wv[1])
            waves.append((sx, sy, cam.S(wv[2] * 26), wv[4] * (1 - wv[2] / wv[3])))
    while len(waves) < 4:
        waves.append((0.0, 0.0, 0.0, 0.0))
    p = dict(
        cam=(cam.x, zoom, base_y, cam.shake_x), depth=depth, floor_back=base_y - depth,
        key=_lin(rig["key"], g["key_i"]), amb=_lin(rig["ambient"], g["amb_i"]),
        key3=g["key3"], sky=_lin(mixc(rig["ambient"], rig["sky_bot"], 0.4), g["amb_i"] * 1.5),
        bounce=_lin(rig["floor"], 0.35 * g["amb_i"]),
        lan=(1.0 * rig["lanterns"], 0.5 * rig["lanterns"], 0.2 * rig["lanterns"]),
        tint=_lin(rig["tint"]), beam_i=g["beam_i"] if cfg.get("beams", True) else 0.0, lan_i=rig["lanterns"],
        refl=g["refl"], wet=g["wet"], rim=g["rim"], emissive=g["emissive"], sky_fill=g["sky_fill"],
        light_dir=g["light_dir"], shadow=g["shadow"] if cfg.get("shadows", True) else (0, 1, 0, 0),
        lanterns=lanterns, fighters=figs, decals=decals, waves=waves, tex=float(cfg.get("tex", 1.0)),
        light_uv=g["light_uv"], rays_i=g["rays"] if cfg.get("beams", True) else 0.0, rays_col=g["rays_col"],
        exposure=g["exposure"], lift=g["lift"], gain=g["gain"], sat=g["sat"], power=g["power"],
        bloom=0.05 if cfg.get("bloom") else 0.02, ca=0.25 + (fx.ca if fx is not None else 0.0),
        vig=g["vig"], grain=0.03 if cfg.get("grain") else 0.0,
        spot=dojo.single_beam, spot_pos=(W / 2, FLOOR_Y + 40),
        impact=(1.0 if fx is not None and fx.impact > 0 else 0.0),
        tilt=cam.tilt if cfg.get("tilt") else 0.0, dof=0.0, dim=0.0, lut="Natural", fxaa=True,
    )
    if extra:
        p.update(extra)
    return p


# ─────────────────────────────────────────────────────────────────────────────
#  RENDERER
# ─────────────────────────────────────────────────────────────────────────────
class GPURenderer:
    BLOOM_LEVELS = 6

    def __init__(self, ctx, offscreen: bool):
        self.ctx = ctx
        self.offscreen = offscreen
        self.t = 0.0
        ctx.enable_only(0)
        vbo = ctx.buffer(np.array([-1, -1, 3, -1, -1, 3], dtype="f4").tobytes())
        self.vbo = vbo

        def prog(fs):
            p = ctx.program(vertex_shader=VERT, fragment_shader=fs)
            return p, ctx.vertex_array(p, [(vbo, "2f", "in_pos")])

        self.p_scene, self.v_scene = prog(SCENE)
        self.p_rays, self.v_rays = prog(RAYS)
        self.p_down, self.v_down = prog(DOWN)
        self.p_up, self.v_up = prog(UP)
        self.p_dof, self.v_dof = prog(DOF)
        self.p_final, self.v_final = prog(FINAL)
        self.p_present, self.v_present = prog(PRESENT)

        def tex(size, comps=4, dtype="f1", mip=False):
            t = ctx.texture(size, comps, dtype=dtype)
            t.repeat_x = False
            t.repeat_y = False
            t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR) if mip else (moderngl.LINEAR, moderngl.LINEAR)
            return t

        self.t_bg = tex((W, H), mip=True)
        self.t_fig = tex((W, H), mip=True)
        self.t_fx = tex((W, H))
        self.t_figd = tex((W, H), mip=True)
        self._figd_blank = None
        self.t_hud = tex((W, H))
        self.t_scene = tex((W, H), dtype="f2")
        self.f_scene = ctx.framebuffer([self.t_scene])
        self.t_dof = tex((W, H), dtype="f2")
        self.f_dof = ctx.framebuffer([self.t_dof])
        self.t_rays = tex((W // 2, H // 2), dtype="f2")
        self.f_rays = ctx.framebuffer([self.t_rays])
        self.bloom = []
        w, h = W, H
        for _ in range(self.BLOOM_LEVELS):
            w, h = max(1, w // 2), max(1, h // 2)
            t = tex((w, h), dtype="f2")
            self.bloom.append((t, ctx.framebuffer([t]), (w, h)))
        self.t_ldr = tex((W, H))
        self.f_ldr = ctx.framebuffer([self.t_ldr])
        self.t_out = tex((W, H))
        self.f_out = ctx.framebuffer([self.t_out])
        self._swz = None

    # ----- creación -----
    @staticmethod
    def available() -> bool:
        return moderngl is not None and np is not None

    @classmethod
    def for_window(cls) -> "GPURenderer":
        ctx = moderngl.create_context()
        return cls(ctx, offscreen=False)

    @classmethod
    def offscreen_context(cls) -> "GPURenderer":
        last = None
        for kw in ({"backend": "egl"}, {}):
            try:
                ctx = moderngl.create_standalone_context(**kw)
                return cls(ctx, offscreen=True)
            except Exception as e:  # pragma: no cover
                last = e
        raise RuntimeError(f"sin contexto OpenGL: {last}")

    @property
    def renderer_name(self) -> str:
        try:
            return self.ctx.info.get("GL_RENDERER", "?")
        except Exception:
            return "?"

    # ----- utilidades -----
    def _upload(self, tex, surf: pygame.Surface, mip=False):
        if self._swz is None:
            masks = surf.get_masks()
            self._swz = "BGRA" if masks[0] == 0xFF0000 else "RGBA"
        tex.swizzle = self._swz
        try:
            tex.write(surf.get_view("1"))
        except Exception:
            tex.swizzle = "RGBA"
            tex.write(pygame.image.tobytes(surf, "RGBA"))
        if mip:
            tex.build_mipmaps()

    @staticmethod
    def _set(prog, name, value):
        if name not in prog:
            return
        u = prog[name]
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
            u.write(np.asarray(value, dtype="f4").tobytes())
        else:
            u.value = value

    def _bind(self, prog, **textures):
        for i, (name, t) in enumerate(textures.items()):
            t.use(location=i)
            self._set(prog, name, i)

    # ----- fotograma -----
    def render(self, p: dict, bg, fig, fx, hud, dt=1 / 60, window_size=None, figd=None):
        ctx = self.ctx
        self.t += dt
        self._upload(self.t_bg, bg, mip=True)
        self._upload(self.t_fig, fig, mip=True)
        if figd is not None:
            self._upload(self.t_figd, figd, mip=True)
        self._upload(self.t_fx, fx)
        self._upload(self.t_hud, hud)
        S = self._set
        ctx.disable(moderngl.BLEND)

        # 1) escena HDR
        pr = self.p_scene
        self._bind(pr, tBg=self.t_bg, tFig=self.t_fig, tFx=self.t_fx, tFigD=self.t_figd)
        S(pr, "uRes", (float(W), float(H)))
        S(pr, "uTime", self.t)
        S(pr, "uFigLit", 1.0 if figd is not None else 0.0)
        S(pr, "uTexI", float(p.get("tex", 1.0)))
        for k, n in (("key3", "uKey3"), ("sky", "uSky"), ("bounce", "uBounce")):
            S(pr, n, tuple(float(v) for v in p[k]))
        S(pr, "uCam", tuple(float(v) for v in p["cam"]))
        S(pr, "uDepthScale", float(p["depth"]))
        S(pr, "uFloorBackY", float(p["floor_back"]))
        for k, n in (("key", "uKey"), ("amb", "uAmb"), ("lan", "uLan"), ("tint", "uTint")):
            S(pr, n, tuple(float(v) for v in p[k]))
        for k, n in (("beam_i", "uBeamI"), ("lan_i", "uLanI"), ("refl", "uRefl"), ("wet", "uWet"), ("rim", "uRimI"),
                     ("emissive", "uEmissive"), ("sky_fill", "uSkyFill")):
            S(pr, n, float(p[k]))
        S(pr, "uLightDir", tuple(float(v) for v in p["light_dir"]))
        S(pr, "uShadow", tuple(float(v) for v in p["shadow"]))
        S(pr, "uLanterns", p["lanterns"])
        S(pr, "uFighters", p["fighters"])
        dec = list(p["decals"]) + [(0.0, 1.0, 0.0, 0.0)] * (16 - len(p["decals"]))
        S(pr, "uDecals", dec[:16])
        S(pr, "uDecalN", len(p["decals"]))
        self.f_scene.use()
        self.v_scene.render(moderngl.TRIANGLES, vertices=3)
        src = self.t_scene

        # 2) profundidad de campo
        if p.get("dof", 0) > 0.3:
            pr = self.p_dof
            self._bind(pr, tScene=self.t_scene)
            S(pr, "uTexel", (1 / W, 1 / H))
            S(pr, "uDof", float(p["dof"]))
            self.f_dof.use()
            self.v_dof.render(moderngl.TRIANGLES, vertices=3)
            src = self.t_dof

        # 3) rayos volumétricos (media resolución)
        pr = self.p_rays
        self._bind(pr, tBg=self.t_bg, tFig=self.t_fig)
        S(pr, "uRes", (float(W), float(H)))
        S(pr, "uLightUV", tuple(float(v) for v in p["light_uv"]))
        S(pr, "uFloorBackY", float(p["floor_back"]))
        S(pr, "uDensity", 0.42)
        S(pr, "uDecay", 0.985)
        S(pr, "uTime", self.t)
        self.f_rays.use()
        if p["rays_i"] > 0.01:
            self.v_rays.render(moderngl.TRIANGLES, vertices=3)
        else:
            self.f_rays.clear(0, 0, 0, 1)

        # 4) bloom: bajada con Karis y subida en tienda
        pr = self.p_down
        prev, pw, ph = src, W, H
        for i, (t, fb, (w, h)) in enumerate(self.bloom):
            self._bind(pr, tSrc=prev)
            S(pr, "uTexel", (1 / pw, 1 / ph))
            S(pr, "uKaris", 1 if i == 0 else 0)
            fb.use()
            self.v_down.render(moderngl.TRIANGLES, vertices=3)
            prev, pw, ph = t, w, h
        pr = self.p_up
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.ONE, moderngl.ONE
        for i in range(self.BLOOM_LEVELS - 1, 0, -1):
            t, _, (w, h) = self.bloom[i]
            self._bind(pr, tSrc=t)
            S(pr, "uTexel", (1 / w, 1 / h))
            self.bloom[i - 1][1].use()
            self.v_up.render(moderngl.TRIANGLES, vertices=3)
        ctx.disable(moderngl.BLEND)

        # 5) composición final, tonemapping y gradación
        pr = self.p_final
        self._bind(pr, tScene=src, tBloom=self.bloom[0][0], tRays=self.t_rays)
        S(pr, "uRes", (float(W), float(H)))
        S(pr, "uTime", self.t)
        for k, n in (("bloom", "uBloom"), ("exposure", "uExposure"), ("rays_i", "uRaysI"), ("ca", "uCA"), ("tilt", "uTilt"),
                     ("vig", "uVig"), ("grain", "uGrain"), ("spot", "uSpot"), ("impact", "uImpact"), ("dim", "uDim"),
                     ("sat", "uSat"), ("power", "uPower")):
            S(pr, n, float(p[k]))
        for k, n in (("rays_col", "uRaysCol"), ("lift", "uLift"), ("gain", "uGain")):
            S(pr, n, tuple(float(v) for v in p[k]))
        S(pr, "uWaves", p["waves"])
        S(pr, "uSpotPos", tuple(float(v) for v in p["spot_pos"]))
        S(pr, "uLut", LUT_IDS.get(p.get("lut", "Natural"), 0))
        S(pr, "uDalt", DALT_IDS.get(p.get("daltonism", "off"), 0))
        S(pr, "uHiContrast", 1 if p.get("contrast") else 0)
        self.f_ldr.use()
        self.v_final.render(moderngl.TRIANGLES, vertices=3)

        # 6) FXAA + HUD
        pr = self.p_present
        self._bind(pr, tLdr=self.t_ldr, tHud=self.t_hud)
        S(pr, "uTexel", (1 / W, 1 / H))
        S(pr, "uFxaa", 1 if p.get("fxaa", True) else 0)
        if self.offscreen:
            self.f_out.use()
            self.v_present.render(moderngl.TRIANGLES, vertices=3)
        else:
            ww, wh = window_size or (W, H)
            s = min(ww / W, wh / H)
            vw, vh = int(W * s), int(H * s)
            scr = ctx.screen
            scr.use()
            scr.viewport = (0, 0, ww, wh)
            scr.clear(0, 0, 0, 1)
            scr.viewport = ((ww - vw) // 2, (wh - vh) // 2, vw, vh)
            self.v_present.render(moderngl.TRIANGLES, vertices=3)

    def read(self, with_hud=True) -> pygame.Surface:
        """Fotograma final como Surface (con HUD) o la imagen limpia sin interfaz (Modo Foto)."""
        if with_hud:
            if not self.offscreen:
                pr = self.p_present
                self._bind(pr, tLdr=self.t_ldr, tHud=self.t_hud)
                self.f_out.use()
                self.v_present.render(moderngl.TRIANGLES, vertices=3)
            data = self.f_out.read(components=3)
        else:
            data = self.f_ldr.read(components=3)
        return pygame.image.frombytes(data, (W, H), "RGB", True)
