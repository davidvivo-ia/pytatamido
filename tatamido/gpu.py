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
import os
from typing import List, Optional

import pygame

try:
    import moderngl
    import numpy as np
except Exception:  # pragma: no cover
    moderngl = None
    np = None

from .config import FLOOR_Y, H, RS, W, cam_base_off, mixc

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
uniform float uRS;            // escala de render (1 = 720p): las medidas en píxeles se multiplican por ella
float LB() { return log2(max(uRS, 1.)); }   // desplazamiento de mip equivalente
vec3 toLin(vec3 c) { return pow(max(c, 0.), vec3(2.2)); }
float luma(vec3 c) { return dot(c, vec3(.2126, .7152, .0722)); }
"""

SCENE = COMMON + """
uniform sampler2D tBg, tFig, tFx, tFigD, tPlate, tPlateM, tPlate2;
uniform float uPlateMix;      // fundido de la lámina anterior (cambio de asalto) a la actual
uniform float uPineSway;      // viento: desplazamiento de la sombra del pino (unidades de pared)
uniform vec4 uWall;           // pared → pantalla: x0, y del borde superior, px por unidad, y del zócalo
uniform vec4 uPlate;          // lámina: u mínima, v mínima, ancho y alto en unidades de pared
uniform float uPlateOn;
uniform vec2 uRes;
uniform float uTime;
uniform vec3 uKey3, uSky, uBounce;
uniform float uFigLit;
uniform float uDimBg;         // menús: oscurece el fondo pero no a los luchadores
uniform float uDebugN;        // depuración: pinta las normales
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
    float h = dy / uShadow.y / uRS;
    vec2 src = vec2(p.x - h * uShadow.x * uRS, yf - h * uRS);
    if (src.y < 1.) return 0.;
    float lod = 0.6 + h * uShadow.z + LB();
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
        vec2 q = vec2((p.x - l.x) / 7., (p.y - ry) / (26. + 40. * uWet)) / uRS;
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
        vec2 pr = p / uRS;
        vec2 cell = floor(pr / vec2(64., 20.));
        vec2 cpos = (cell + vec2(hash12(cell), hash12(cell + 7.))) * vec2(64., 20.);
        float t = fract(uTime * 0.9 + hash12(cell + 3.));
        vec2 q = (pr - cpos) * vec2(1., 2.8);
        float r = length(q);
        float ring = sin(r * 0.7 - t * 22.) * exp(-r * 0.07) * (1. - t) * step(r, t * 48.);
        rip = normalize(q + 1e-3) * ring * 4. * uRS;
    }
    float dy = p.y - yf;
    if (dy > 0.) {
        float dr = dy / uRS;
        vec4 rf = Lp(tFig, vec2(p.x, yf - dy) + rip, 1. + dr * 0.03 * (1. - 0.5 * uWet) + LB());
        col += toLin(rf.rgb) * uTint * rf.a * refl * exp(-dr * 0.009);
    }
    float dw = p.y - uFloorBackY;
    if (dw > 0.) {
        float dwr = dw / uRS;
        vec2 rp = vec2(p.x, uFloorBackY - dw * 1.4) + rip;
        vec4 wb = Lp(tBg, rp, 1.5 + dwr * 0.025 + LB());
        vec3 wl = toLin(wb.rgb);
        if (uPlateOn > 0.5) {               // reflejo de la lámina (textura desenfocada por la distancia)
            vec2 w = vec2((rp.x - uWall.x) / uWall.z, (rp.y - uWall.y) / uWall.z);
            vec3 pl = toLin(textureLod(tPlate, (w - uPlate.xy) / uPlate.zw, 2. + dwr * 0.03).rgb);
            wl = mix(pl * (1. + uEmissive * smoothstep(0.3, 0.8, luma(pl))), wl, wb.a);
            col += wl * refl * 0.7 * exp(-dwr * 0.011);
        } else
        col += wl * (1. + uEmissive * smoothstep(0.3, 0.8, luma(wl))) * wb.a * refl * 0.7 * exp(-dwr * 0.011);
    }
    col += lspec * gloss * uLanI * (0.25 + uWet);
    return col;
}

// ── luz por píxel de los luchadores ──────────────────────────────────────────
// tFigD: R altura (grupos de profundidad y surcos), G pieza, B brillo. El volumen sale de tForm.
// 0 tinta · 1 piel · 2 algodón · 3 satén/lycra · 4 cuero · 5 pelo · 6 ojos
float figH(vec2 q) { return Lp(tFigD, q, 1.0 + LB()).r * 0.5 + Lp(tFigD, q, 2.3 + LB()).r * 0.5; }

// ── volumen: pendiente de la forma (pase FORM sobre el campo de distancias, ya suavizada) ──
uniform sampler2D tForm;
vec2 formSlope(vec2 p) { vec4 F = texture(tForm, p / uRes); return F.w > 1e-3 ? F.xy / F.w : vec2(0.); }
// Altura de la figura en px hacia la cámara: capa de profundidad (brazo delante del torso) + perfil.
float figHeight(vec2 q, float kg) {
    vec4 F = texture(tForm, q / uRes);
    float hp = F.w > 1e-3 ? F.z / F.w : 0.;
    float g = texelFetch(tFigD, ivec2(clamp(q, vec2(0.), uRes - 1.)), 0).r * 255.;
    return g * kg + hp;
}
// Autosombra: ¿algo de la propia figura tapa la luz? Marcha por el campo de alturas hacia la luz.
float selfShadow(vec2 p, vec3 L, float pxcm) {
    vec2 lxy = vec2(L.x, -L.y);
    float lh = length(lxy);
    if (lh < 0.05) return 1.;
    vec2 dir = lxy / lh;
    float rise = L.z / lh;                                  // px de altura por px de avance
    float kg = 0.3 * pxcm;                                  // una unidad de capa ≈ 3 mm
    float h0 = figHeight(p, kg) + 0.6 * pxcm;
    float maxT = 42. * pxcm;
    float occ = 0.;
    // pasos fijos (sin ruido: el ruido se veía como un moteado en la piel); la penumbra crece con la distancia
    for (int i = 1; i <= 16; i++) {
        float t = maxT * pow(float(i) / 16., 1.7) + 1.5 * uRS;
        float dh = figHeight(p + dir * t, kg) - (h0 + t * rise);
        occ = max(occ, smoothstep(0., (1.5 + t / pxcm * 0.12) * pxcm, dh));
    }
    return 1. - occ;
}

// ── tejidos diseñados, en el espacio propio de cada pieza ────────────────────
// El mapa de datos guarda en G el número de pieza; tPieces trae su ancla en pantalla, su eje, los
// px por cm, el material, el tejido, una semilla y el ancho. Con eso cada píxel sabe dónde está
// dentro de su pieza en cm (q.x a lo largo, q.y a través): el tejido sigue a la manga, a la pernera
// o al mechón, no resbala aunque el miembro se mueva y gira con él. Cada patrón da una altura (relieve
// para la luz), un factor de albedo, un factor de brillo y, en el cinturón gastado, desgaste.
// Todo detalle se funde a su media antes de llegar al tamaño de un píxel.
uniform sampler2D tPieces;

float bump1(float x) { return sin(3.14159265 * fract(x)); }          // cresta redonda por periodo
float vis(float P, float pxcm) { return smoothstep(1.7, 3.6, P * pxcm); }

// caída de la tela: pliegues suaves que corren a lo largo de la pieza, a ratos más marcados
float drape(vec2 q, float seed, float sc) {
    float warp = (vnoise(vec2(q.x * 0.06 / sc + seed, 1.7)) - 0.5) * 1.6;
    float u = q.y / (2.6 * sc) + warp;
    float n = vnoise(vec2(u, seed * 0.7 + q.x * 0.025 / sc));      // ondulación irregular, no rayas
    float n2 = vnoise(vec2(u * 2.3 + 5., seed + q.x * 0.04 / sc));
    return smoothstep(0.12, 0.88, n * 0.72 + n2 * 0.28);
}

void weave(int pat, vec2 q, float pxcm, float seed, float wcm, out float h, out float ak, out float sk, out float wear) {
    h = 0.; ak = 1.; sk = 1.; wear = 0.;
    if (pat == 1) {                                  // judogi: grano de arroz acolchado, hileras al tresbolillo
        vec2 c = q / vec2(1.2, 0.8);
        float row = floor(c.y);
        c.x += mod(row, 2.) * 0.5;
        vec2 f = fract(c) - 0.5;
        float grain = 1. - smoothstep(0.22, 0.5, length(f * vec2(1., 1.25)));
        float v = vis(0.8, pxcm);
        float t = hash12(floor(c) + seed);
        float zone = 0.55 + 0.45 * smoothstep(0.3, 0.7, vnoise(q * 0.07 + seed));   // el grano se marca más donde tira la tela
        h = (grain * grain * 0.3 - 0.1) * v * zone + drape(q, seed, 1.25) * 0.5;
        ak = 1. + v * zone * (0.045 * grain - 0.025 + 0.015 * (t - 0.5));
        sk = mix(1., 0.7 + 0.4 * grain, v);
    } else if (pat == 2) {                           // sarga: costillas en diagonal
        float wale = bump1((q.x * 0.7 + q.y) / 1.1);
        float v = vis(1.1, pxcm);
        h = wale * 0.2 * v + drape(q, seed, 1.0) * 0.5;
        ak = 1. + v * (0.04 * wale - 0.02);
    } else if (pat == 3) {                           // lona de cesta: parejas de hilos que alternan
        vec2 c = q / 1.6;
        vec2 ci = floor(c), f = fract(c);
        float u = mod(ci.x + ci.y, 2.) < 0.5 ? f.y : f.x;
        float rib = bump1(u * 2.);
        float e = smoothstep(0., 0.12, min(min(f.x, 1. - f.x), min(f.y, 1. - f.y)));
        float v = vis(0.8, pxcm);
        h = (rib * e * 0.28 - 0.12) * v + drape(q, seed, 1.5) * 0.5;
        ak = 1. + v * (0.05 * rib * e - 0.03);
    } else if (pat == 4) {                           // punto de canalé
        float y = q.y / 0.9;
        float rib = bump1(y);
        float v = vis(0.9, pxcm);
        h = rib * 0.25 * v + drape(q, seed, 0.8) * 0.4;
        ak = 1. + v * (0.04 * rib - 0.02);
    } else if (pat == 5) {                           // damasco de seda: medallones tono sobre tono
        vec2 c = q / 8.;
        c.x += mod(floor(c.y), 2.) * 0.5;
        vec2 f = (fract(c) - 0.5) * 8.;
        float r = length(f);
        float th = atan(f.y, f.x);
        float ring = 1. - smoothstep(0.12, 0.3, abs(r - 2.7));
        float petal = smoothstep(0.4, 0.8, 0.5 + 0.5 * cos(4. * th)) * smoothstep(0.5, 0.9, r) * (1. - smoothstep(1.6, 2.1, r));
        float dotc = 1. - smoothstep(0.35, 0.55, r);
        vec2 g = abs(fract(c + 0.5) - 0.5) * 8.;
        float dia = 1. - smoothstep(0.55, 0.8, g.x + g.y);
        float motif = max(max(ring, petal), max(dotc, dia));
        float v = vis(0.6, pxcm);
        h = motif * 0.18 * v + drape(q, seed, 1.0) * 0.75;
        ak = 1. + 0.5 * motif * v;
        sk = 0.6 + 2.0 * motif * v;
    } else if (pat == 6) {                           // lino: hilos irregulares con nudos (slubs)
        float rc = q.x / 1.1;
        float ri = floor(rc);
        float thread = pow(max(bump1(rc), 0.), 0.6 + 0.8 * hash12(vec2(ri, seed)));
        float slub = smoothstep(0.55, 0.8, vnoise(vec2(ri * 3.7, q.y * 0.22 + seed)));
        float warp = bump1(q.y / 1.3);
        float v = vis(1.1, pxcm);
        h = (thread * 0.18 + warp * 0.08 + slub * 0.18) * v + drape(q, seed, 1.3) * 0.5;
        ak = 1. + v * (0.035 * (hash12(vec2(ri, seed + 3.)) - 0.5) + 0.03 * slub - 0.02 * (1. - thread));
    } else if (pat == 7 || pat == 16) {              // cinturón: pespuntes paralelos a lo largo
        float rows = q.y / 0.7;
        float line = 1. - smoothstep(0.06, 0.2, abs(fract(rows) - 0.5));
        float fx = fract(q.x / 0.9);
        float dash = smoothstep(0.12, 0.28, fx) * (1. - smoothstep(0.72, 0.88, fx));
        float v = vis(0.7, pxcm);
        h = (bump1(rows) * 0.18 - line * (0.35 + 0.3 * dash)) * v;
        ak = 1. - 0.2 * line * v;
        sk = 1. - 0.5 * line * v;
        if (pat == 16) {                             // cinturón negro gastado: asoma el alma clara
            float n = fbm(vec2(q.x * 0.33, q.y * 1.15) + seed);
            float edge = smoothstep(wcm * 0.22, wcm * 0.5, abs(q.y));
            wear = 0.8 * smoothstep(0.52, 0.74, n + edge * 0.3);
        }
    } else if (pat == 8) {                           // satén y lycra: grandes pliegues que atrapan la luz
        h = drape(q, seed, 1.7) * 1.1;
    } else if (pat == 9) {                           // mawashi: seda gruesa acanalada
        float rib = pow(max(bump1(q.y / 1.7), 0.), 0.6);
        float v = vis(1.7, pxcm);
        h = rib * 0.28 * v + drape(q, seed, 2.5) * 0.08;
        ak = 1. + v * (0.1 * rib - 0.06);
        sk = 0.6 + 0.8 * rib;
    } else if (pat == 10) {                          // cuero: arrugas transversales y granulado
        float cr = sin(6.2831853 * (q.x / 2.6 + 0.35 * vnoise(q * 0.45 + seed)));
        float crease = smoothstep(0.8, 0.98, cr) * smoothstep(0.35, 0.6, vnoise(vec2(q.x * 0.18, q.y * 0.35) + seed * 2.));
        float peb = vnoise(q * 1.9 + seed);
        float v = vis(0.55, pxcm);
        h = -crease * 0.6 + (peb - 0.5) * 0.25 * v;
        ak = 1. - 0.2 * crease;
        sk = 1. - 0.6 * crease + 0.3 * (peb - 0.5) * v;
    } else if (pat == 11) {                          // neopreno con canales
        float rib = bump1(q.x / 1.3);
        float v = vis(1.3, pxcm);
        h = rib * 0.6 * v;
        ak = 1. + v * (0.16 * rib - 0.1);
        sk = 0.5;
    } else if (pat == 12) {                          // pelo: mechones y hebras que siguen la caída
        float s = q.y + 2.6 * (vnoise(vec2(q.x * 0.22, seed)) - 0.5) + 0.8 * (vnoise(q * 0.5 + seed) - 0.5);
        float ci = floor(s / 0.95);
        float clump = bump1(s / 0.95);
        float fine = 0.5 + 0.5 * sin(6.2831853 * s / 0.38 + hash12(vec2(ci, seed)) * 6.);
        float v = vis(0.95, pxcm), v2 = vis(0.38, pxcm);
        h = clump * 0.3 * v + fine * 0.1 * v2;
        ak = 1. + v * (0.1 * clump - 0.05 + 0.08 * (hash12(vec2(ci, seed + 1.3)) - 0.5)) + v2 * 0.05 * (fine - 0.5);
        sk = 0.75 + 0.35 * clump;
    } else if (pat == 14) {                          // solapa: tres pespuntes a lo largo
        float a = abs(q.y) / (max(wcm, 0.5) * 0.5);
        float rows = a * 3.;
        float fx = fract(q.x / 0.8);
        float dash = smoothstep(0.1, 0.25, fx) * (1. - smoothstep(0.7, 0.85, fx));
        float line = (1. - smoothstep(0.06, 0.22, abs(fract(rows) - 0.5))) * step(a, 0.95) * dash;
        float v = vis(max(wcm, 0.5) / 3., pxcm);
        h = (bump1(rows) * 0.25 - line * 0.5) * v;
        ak = 1. - 0.15 * line * v;
    } else if (pat == 15) {                          // algodón de tafetán
        float wv = bump1(q.x / 0.9) * bump1(q.y / 0.9);
        float v = vis(0.9, pxcm);
        h = wv * 0.15 * v + drape(q, seed, 1.1) * 0.5;
        ak = 1. + v * (0.03 * wv - 0.015);
    }
    // ── segunda octava: el hilo. Sólo aparece cuando hay píxeles para verlo (1440p, 4K, Modo Foto) ──
    float vf = vis(0.32, pxcm);
    if (vf > 0.01) {
        if (pat == 1 || pat == 3 || pat == 15 || pat == 6) {           // tafetán: urdimbre y trama que se cruzan
            float over = mod(floor(q.x / 0.22) + floor(q.y / 0.22), 2.);
            float th = mix(bump1(q.x / 0.22), bump1(q.y / 0.22), over);
            h += (th - 0.5) * 0.12 * vf;
            ak *= 1. + (th - 0.5) * 0.05 * vf;
        } else if (pat == 2 || pat == 4) {                              // sarga y punto: costillas finas
            float th = bump1((q.x * 0.6 + q.y) / 0.3);
            h += (th - 0.5) * 0.1 * vf;
            ak *= 1. + (th - 0.5) * 0.045 * vf;
        } else if (pat == 8 || pat == 9 || pat == 5) {                  // satén y seda: flotes finos a lo largo
            float th = vnoise(vec2(q.x * 1.2, q.y * 9.) + seed);
            sk *= 1. + (th - 0.5) * 0.6 * vf;
            h += (th - 0.5) * 0.04 * vf;
        } else if (pat == 13) {                                         // piel: poros
            float po = vnoise(q * 7.5 + seed);
            h += (po - 0.5) * 0.05 * vis(0.14, pxcm);
            sk *= 0.8 + 0.4 * po;
        } else if (pat == 12) {                                         // pelo: hebras sueltas
            float v3 = vis(0.16, pxcm);
            float fs = 0.5 + 0.5 * sin(6.2831853 * (q.y + 0.3 * vnoise(vec2(q.x * 0.3, seed))) / 0.16);
            h += (fs - 0.5) * 0.12 * v3;
            ak *= 1. + (fs - 0.5) * 0.08 * v3;
        } else if (pat == 10 || pat == 11) {                            // cuero y neopreno: grano
            float gr = vnoise(q * 4.2 + seed);
            h += (gr - 0.5) * 0.08 * vis(0.24, pxcm);
            sk *= 0.85 + 0.3 * gr;
        }
    }
}

vec3 hue_(vec3 c) { return c / max(max(c.r, c.g), max(c.b, 1e-3)); }

vec3 litFighter(vec2 p, vec4 fg) {
    vec3 alb = toLin(fg.rgb);
    int id = int(texelFetch(tFigD, ivec2(p), 0).g * 255. + 0.5);
    if (id == 0) return alb * uTint;                       // tinta: el contorno no se ilumina
    vec4 fd = Lp(tFigD, p);
    vec4 P0 = texelFetch(tPieces, ivec2(id, 0), 0);
    vec4 P1 = texelFetch(tPieces, ivec2(id, 1), 0);
    int mat = int(P1.x + 0.5);
    int pat = int(P1.y + 0.5);
    float seed = P1.z, wcm = P1.w;
    float pxcm = max(P0.w, 0.05);
    vec2 t = vec2(cos(P0.z), sin(P0.z));
    vec2 nn = vec2(-t.y, t.x);
    vec2 dd = p - P0.xy;
    vec2 q = vec2(dot(dd, t), dot(dd, nn)) / pxcm;         // cm dentro de la pieza

    float e = 2.0 * uRS;
    float hx = figH(p + vec2(e, 0.)) - figH(p - vec2(e, 0.));
    float hy = figH(p + vec2(0., e)) - figH(p - vec2(0., e));
    float bump = mat == 5 ? 5.0 : 7.0;
    // forma: cilindro de la anchura local real (campo de distancias); el pelo y las telas sueltas, menos
    float round_ = mat == 5 ? 0.7 : mat == 6 ? 0. : 1.0;
    vec2 fs = formSlope(p) * round_;                       // pendiente de la forma
    // la altura desenfocada sólo aporta los escalones entre capas (brazo sobre torso)
    float e2 = 6.0 * uRS, l2 = 3.6 + LB();
    float bx = Lp(tFigD, p + vec2(e2, 0.), l2).r - Lp(tFigD, p - vec2(e2, 0.), l2).r;
    float by = Lp(tFigD, p + vec2(0., e2), l2).r - Lp(tFigD, p - vec2(0., e2), l2).r;
    float formK = 0.9;
    vec3 N0 = normalize(vec3(fs.x - hx * bump - bx * formK, fs.y + hy * bump + by * formK, 1.0));
    // tejido: valor y pendiente (diferencias de medio píxel), girada de la pieza a la pantalla
    float h, ak, sk, wear, h1, h2, a_, s_, w_;
    weave(pat, q, pxcm, seed, wcm, h, ak, sk, wear);
    float es = 0.6 / pxcm;
    weave(pat, q + vec2(es, 0.), pxcm, seed, wcm, h1, a_, s_, w_);
    weave(pat, q + vec2(0., es), pxcm, seed, wcm, h2, a_, s_, w_);
    vec2 gq = vec2(h1 - h, h2 - h) / es;
    vec2 gs = (t * gq.x + nn * gq.y) / pxcm * uTexI;
    float tb = mat == 4 ? 0.5 : mat == 5 ? 0.55 : 0.7;
    vec3 N = normalize(vec3(fs.x - hx * bump - bx * formK - gs.x * tb, fs.y + hy * bump + by * formK + gs.y * tb, 1.0));
    alb *= mix(1., ak, uTexI);
    if (wear > 0.) {
        float fib = 0.75 + 0.25 * bump1(q.y / 0.35 + 0.3 * q.x);
        alb = mix(alb, vec3(0.34, 0.32, 0.29) * fib, wear * uTexI);
    }
    if (mat == 4) alb *= 1. + 0.45 * smoothstep(0.3, 0.62, 1. - N0.z) * uTexI;     // cuero gastado en los cantos
    // bajo la lluvia: la tela empapada oscurece y se pega, el pelo se apelmaza; todo brilla más
    float wetK = uWet;
    if (wetK > 0.) {
        if (mat == 2 || mat == 3) alb *= mix(1., 0.6, wetK);
        else if (mat == 5) alb *= mix(1., 0.72, wetK);
        else if (mat == 1) alb *= mix(1., 0.9, wetK);
    }
    if (uDebugN > 0.5 && uDebugN < 1.5) return toLin(N * 0.5 + 0.5) / max(uTint, vec3(0.05));

    float ao = 1. - clamp((Lp(tFigD, p, 4.2 + LB()).r - fd.r) * 2.2, 0., 0.5);
    // oclusión de contacto: lo que se mete bajo otra parte (axila, entrepierna, bajo la barbilla o el cinturón)
    // recibe menos cielo. Horizonte en 8 direcciones y dos radios sobre el campo de alturas.
    {
        float kg = 0.3 * pxcm;
        float h0c = figHeight(p, kg);
        float occ = 0.;
        for (int i = 0; i < 8; i++) {
            float a = float(i) * 0.785398 + 0.39;
            vec2 dir = vec2(cos(a), sin(a));
            for (int j = 1; j <= 2; j++) {
                float r = float(j) * 4.5 * pxcm;
                float dh = figHeight(p + dir * r, kg) - h0c - 0.8 * pxcm;
                occ += clamp(dh / r, 0., 1.) * (j == 1 ? 0.6 : 0.4);
            }
        }
        ao *= 1. - clamp(occ / 8. * 1.6, 0., 0.55);
    }
    vec3 V = vec3(0., 0., 1.);
    vec3 L = normalize(uKey3);
    vec3 Hv = normalize(L + V);
    float spec = fd.b * mix(1., sk, uTexI);
    float ndl = dot(N, L);
    // pincelada: el borde de la sombra se pinta a lo largo de la forma y la sombra lleva trama
    float paint = mat == 6 ? 0. : uTexI;
    float br = vnoise(vec2(q.x * 0.2, q.y * 1.05) + seed * 3.1);
    float br2 = vnoise(vec2(q.x * 0.45, q.y * 2.1) + seed * 5.7) * vis(0.5, pxcm);
    // ── rampa de dos tonos (anime 2026): borde pintado, sombra de color desplazado ──
    float soft = mat == 1 ? 0.15 : 0.09;
    float term = ndl + paint * ((br - 0.5) * 0.16 + (br2 - 0.25) * 0.05);
    float shadowSelf = mat == 6 ? 1. : selfShadow(p, L, pxcm);
    if (uDebugN > 1.5) return toLin(uDebugN > 2.5 ? vec3(fract(figHeight(p, 0.3 * pxcm) / (60. * pxcm))) : vec3(shadowSelf)) / max(uTint, vec3(0.05));
    float lit = smoothstep(-0.02 - soft, -0.02 + soft, term) * mix(1.0, ao, 0.6) * mix(1., shadowSelf, 0.9);
    vec3 amb = mix(uBounce, uSky, N.y * 0.5 + 0.5) * ao;
    vec3 shTint = mat == 1 ? vec3(1.06, 0.74, 0.68) : mat == 5 ? vec3(0.9, 0.86, 1.05) : vec3(0.84, 0.9, 1.08);
    vec3 shadowCol = alb * (amb * 1.15 + uKey * 0.1) * shTint * (1. + paint * 0.06 * (br - 0.5));
    // cara iluminada con gradiente de forma (medio tono hacia el terminador, pleno donde mira a la luz)
    vec3 litCol = alb * (amb + uKey * (0.58 + 0.5 * smoothstep(-0.05, 0.85, ndl)));
    vec3 col = mix(shadowCol, litCol, lit);
    if (mat == 1) {
        col += alb * uKey * vec3(1.0, 0.3, 0.16) * 1.0 * lit * (1. - lit);              // dispersión en el borde de sombra
        col = mix(col, vec3(luma(col)) * vec3(1.02, 0.98, 0.97), 0.2 * uTexI);           // luz cálida sobre piel cálida: sin plástico
        // rojeces: la sangre asoma donde la piel es fina, en los extremos de cada miembro (rodilla, codo, nudillos)
        float ends = smoothstep(0.55, 0.95, abs(q.x) / max(wcm * 1.6, 4.));
        col *= mix(vec3(1.), vec3(1.06, 0.93, 0.92), ends * 0.6 * uTexI);
    }
    if (mat == 2) col += alb * uKey * 0.2 * pow(1. - N.z, 2.) * lit;                     // pelusa del algodón a contraluz
    // farolillos: luces puntuales con la misma rampa
    vec3 P3 = vec3(p.x, -p.y, 0.) / uRS;
    float lanHi = 0.;
    for (int i = 0; i < 4; i++) {
        vec4 l = uLanterns[i];
        if (l.w <= 0.) continue;
        vec3 d = vec3(l.x / uRS, -l.y / uRS, 140.) - P3;
        float r = length(d);
        vec3 Ld = d / r;
        float att = l.w * 160000. / (r * r + 40000.);
        float ramp = smoothstep(-0.12, 0.12, dot(N, Ld));
        vec3 lanC = mat == 1 ? mix(uLan, vec3(luma(uLan)) * 1.6, 0.35) : uLan;      // la piel no se vuelve arcilla
        col += alb * lanC * att * ramp * (mat == 1 ? 0.45 : 0.6);
        lanHi += att * smoothstep(0.94, 0.975, dot(N, normalize(Ld + V)));
    }
    // brillos duros por material; satén, seda y pelo en vetas anisótropas a lo largo de la pieza
    vec3 T3 = normalize(vec3(t.x, -t.y, 0.0));
    vec3 Na = normalize(N - T3 * dot(N, T3) * 0.9);
    float nh = dot(N, Hv), nha = dot(Na, Hv);
    float hi = 0.;
    if (mat == 1) {
        // brillo graso y de sudor: duro en las zonas que lo tienen (frente, nariz, pómulo, hombros, nudillos:
        // parches de brillo del mapa de datos) y una película suave en el resto
        float zone = smoothstep(0.35, 0.75, spec);
        hi = smoothstep(0.955 - 0.05 * zone, 0.985, nh) * (0.06 + 0.5 * spec) + smoothstep(0.86, 0.97, nh) * 0.04 * (0.6 + br)
           + zone * smoothstep(0.8, 0.95, nh) * 0.12;
    }
    else if (mat == 3) hi = smoothstep(0.84, 0.96, nha) * (pat == 9 ? 0.18 : 0.4) * mix(1., sk, uTexI) + pow(1. - N.z, 3.) * 0.1;
    else if (mat == 4) hi = smoothstep(0.9, 0.97, nh) * 0.7 * spec;                                     // cuero
    else if (mat == 5) {                                                                           // anillo del pelo
        float band = smoothstep(0.9, 0.975, dot(normalize(Na + vec3(0., 0.35, 0.)), Hv)) * mix(1., sk, uTexI);
        hi = band * (0.1 + 0.25 * spec) * (0.3 + 2.2 * sqrt(luma(alb)));        // en pelo negro, brillo frío y contenido
    }
    else if (mat == 2) hi = smoothstep(0.62, 0.72, 1. - N.z) * 0.1 + (pat == 5 ? smoothstep(0.8, 0.94, nha) * 0.35 * sk : 0.);
    float drop = 0.;
    if (wetK > 0. && mat != 6) {
        // película de agua: brillo especular duro en cualquier material
        hi += wetK * smoothstep(0.92, 0.985, nh) * (mat == 1 ? 0.55 : mat == 5 ? 0.45 : 0.4);
        // gotas que resbalan: columnas de ~2 cm, cada una con su gota cayendo a su ritmo
        vec2 dq = vec2(p.x / (2.2 * pxcm), p.y / (11. * pxcm));
        float colx = floor(dq.x);
        float spd = 0.35 + 0.5 * hash12(vec2(colx, 7.3));
        dq.y -= uTime * spd + hash12(vec2(colx, 1.9)) * 9.;
        vec2 ci = vec2(colx, floor(dq.y));
        vec2 cf = fract(dq);
        float on = step(0.7, hash12(ci + seed * 0.37));
        float x0 = 0.3 + 0.4 * hash12(ci + 3.1);
        float head = smoothstep(0.62, 0.78, cf.y) * smoothstep(0.98, 0.84, cf.y);
        float tail = smoothstep(0.1, 0.7, cf.y) * (1. - smoothstep(0.78, 0.84, cf.y)) * 0.35;
        drop = on * smoothstep(0.2, 0.0, abs(cf.x - x0)) * (head + tail) * wetK * vis(2.2, pxcm);
        hi += drop * 0.9;
    }
    vec3 hue = alb / max(max(alb.r, alb.g), max(alb.b, 1e-3));
    vec3 hiCol = mat == 5 ? mix(vec3(0.78, 0.82, 0.95) * luma(uKey) * 1.2, uKey, clamp(luma(alb) * 3., 0., 1.))
               : mat == 3 || pat == 5 || pat == 9 ? uKey * mix(vec3(1.), hue, 0.65)          // la seda tiñe su brillo
               : uKey;
    col += (hiCol * lit + uLan * uLanI * 0.4) * hi + uLan * lanHi * (mat == 1 ? 0.22 : mat == 4 ? 0.6 : mat == 5 ? 0.05 : 0.15);
    // contraluz recortado: una línea nítida de luz en la silueta del lado de la luz (con las normales de forma
    // reales: sólo en los cantos, no en las mellas interiores), un halo fino de los shōji a la espalda en todo
    // el contorno alto, y los farolillos desde arriba
    vec2 nxy = normalize(N0.xy + 1e-4);
    float edge = smoothstep(0.62, 0.86, 1. - N0.z);
    float keySide = smoothstep(0.15, 0.6, dot(nxy, normalize(vec2(L.x, L.y) + 1e-4)));
    float rimK = edge * keySide * (mat == 6 ? 0. : 1.);
    col += uKey * uRimI * 0.75 * rimK * mix(vec3(1.), hue_(alb), 0.25);
    float back = smoothstep(0.78, 0.95, 1. - N0.z) * (0.45 + 0.55 * smoothstep(-0.2, 0.8, N0.y));
    col += mix(uKey, uSky * 2., 0.4) * uRimI * 0.28 * back * (mat == 6 ? 0. : 1.);
    float rimM = smoothstep(0.5, 0.62, 1. - N.z);
    col += uLan * uLanI * 0.6 * rimM * smoothstep(0.2, 0.7, N.y);
    // piel translúcida: orejas, dedos, nariz y bordes finos se encienden en rojo a contraluz
    if (mat == 1) {
        float thin = 1. - smoothstep(1.6, 5.0, wcm);
        float trans = thin * (0.35 + 0.65 * edge) * (0.5 + 0.5 * uRimI);
        col += alb * vec3(1.0, 0.32, 0.22) * (uKey * 0.55 + uLan * uLanI * 0.6) * trans;
    }
    // degradado de ilustración: los pies se enfrían y oscurecen un poco, la cabeza recibe la luz
    vec4 fr = abs(p.x - uFighters[0].x) < abs(p.x - uFighters[1].x) ? uFighters[0] : uFighters[1];
    float hgt = clamp((fr.y - fr.z - p.y) / (pxcm * 175.), 0., 1.);
    col *= mix(vec3(0.86, 0.9, 1.0), vec3(1.04, 1.0, 0.97), smoothstep(0.0, 0.9, hgt));
    return col * uTint;
}


// ── la pared del dojo: lámina horneada y proyectada con la cámara ────────────
// La pared (techo, enlucido, madera, shōji, tokonoma, farolillos) se hornea una vez a alta resolución;
// aquí se proyecta con la transformación de la cámara y se le pinta textura a resolución nativa:
// washi con pulpa y fibras, veta de madera con anillos y tablas, enlucido de barro.
vec3 shadeWall(vec2 p) {
    vec2 w = vec2((p.x - uWall.x) / uWall.z, (p.y - uWall.y) / uWall.z);      // unidades de pared (≈ cm)
    vec2 t = (w - uPlate.xy) / uPlate.zw;
    vec3 c = toLin(texture(tPlate, t).rgb);
    if (uPlateMix < 0.999) c = mix(toLin(texture(tPlate2, t).rgb), c, uPlateMix);
    c *= uTint;
    vec3 m = texture(tPlateM, t).rgb;
    float fpx = uWall.z;
    // sombra del pino del jardín: va en el alfa de la máscara y se mece con el viento sobre el papel
    float pine = 1. - texture(tPlateM, t - vec2(uPineSway / uPlate.z, 0.)).a;
    c = mix(c, c * 0.24, pine * m.r);
    if (m.r > 0.01) {                                  // washi: nubes de pulpa y fibras largas de kōzo
        float pulp = fbm(w * 0.32 + 3.1);
        float fib = 0.;
        for (int i = 0; i < 4; i++) {
            float a = float(i) * 1.37 + 0.4;
            vec2 q = mat2(cos(a), -sin(a), sin(a), cos(a)) * w;
            float n = vnoise(vec2(q.x * 0.28, q.y * 5.5) + float(i) * 17.3);
            fib += smoothstep(0.8, 0.96, n);
        }
        float paper = 0.88 + 0.2 * pulp + 0.07 * fib * vis(0.25, fpx);
        c = mix(c, c * paper, m.r);
    }
    if (m.g > 0.01) {                                  // madera: veta larga, anillos y poros
        float warp = fbm(vec2(w.x * 0.015, w.y * 0.12) + 7.);
        float grain = fbm(vec2(w.x * 0.02, w.y * 0.55));
        float rings = 0.5 + 0.5 * sin((w.y + warp * 38.) * 1.3);
        float pores = vnoise(vec2(w.x * 0.08, w.y * 4.0));
        float wv = 0.8 + 0.26 * grain + 0.08 * rings * vis(2.4, fpx) + 0.06 * (pores - 0.5) * vis(0.3, fpx);
        c = mix(c, c * wv, m.g);
    }
    if (m.b > 0.01) {                                  // enlucido de barro: manchas y paja picada
        float pl = 0.9 + 0.14 * fbm(w * 0.1 + 11.) + 0.05 * (vnoise(w * 1.6) - 0.5) * vis(0.6, fpx);
        float straw = smoothstep(0.86, 0.97, vnoise(vec2(w.x * 0.9 + w.y * 0.4, w.y * 0.2) * 1.3));
        c = mix(c, c * (pl - 0.06 * straw * vis(0.4, fpx)), m.b);
    }
    c *= 1. + uEmissive * smoothstep(0.3, 0.8, luma(c)) * 2.2 * (0.35 + 0.65 * m.r);      // papel a contraluz
    // los farolillos iluminan la madera y el enlucido de alrededor; su papel parpadea con la llama
    for (int i = 0; i < 4; i++) {
        vec4 l = uLanterns[i];
        if (l.w <= 0.) continue;
        vec2 d = (p - l.xy) / uWall.z;
        float body = smoothstep(1.1, 0.85, length(d / vec2(13., 20.)));
        c *= mix(1., 0.78 + 0.32 * l.w / max(uLanI, 1e-3), body);
        float att = l.w * 2600. / (dot(d, d) + 900.);
        c += c * uLan * att * (1. - 0.7 * m.r) * 1.6;
    }
    return c;
}

void main() {
    vec2 p = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y);
    vec4 bg = Lp(tBg, p);
    float coc = 1.;
    vec3 col = vec3(0.);
    if (uPlateOn > 0.5) {
        // pared horneada detrás; en tBg sólo queda lo que se mueve (público, hoja)
        if (p.y < uFloorBackY) col = shadeWall(p);
        else col = shadeFloor(p, coc);
        col = mix(col, toLin(bg.rgb) * uTint, bg.a);
    } else {
        if (bg.a < 0.999) col = shadeFloor(p, coc);
        vec3 b = toLin(bg.rgb) * uTint;
        if (p.y < uFloorBackY) b *= 1. + uEmissive * smoothstep(0.3, 0.8, luma(b)) * 2.2;
        col = mix(col, b, bg.a);
    }
    coc = mix(coc, p.y < uFloorBackY ? 1. : 0.75, bg.a);

    vec4 fg = Lp(tFig, p);
    if (fg.a > 0.004 && uFigLit > 0.5) {
        col = mix(col, litFighter(p, fg), fg.a);
        coc = mix(coc, 0., fg.a);
    } else if (fg.a > 0.004) {
        vec3 c = toLin(fg.rgb) * uTint;
        vec2 ld = normalize(uLightDir);
        float aL = Lp(tFig, p + ld * 3.5 * uRS).a;
        float aS = Lp(tFig, p - ld * 5. * uRS).a;
        c *= 1. - 0.32 * (1. - aS) * fg.a;
        c += fg.a * (1. - aL) * uKey * uRimI;
        float aU = Lp(tFig, p + vec2(0., -3. * uRS)).a;
        c += fg.a * (1. - aU) * uLan * uLanI * 0.9;
        col = mix(col, c, fg.a);
        coc = mix(coc, 0., fg.a);
    }
    vec4 fx = Lp(tFx, p);
    vec3 fc = toLin(fx.rgb);
    float lf = luma(fc);
    col = mix(col, fc * (1. + 3. * lf * lf), fx.a);
    col *= 1. - uDimBg * (1. - fg.a);
    frag = vec4(col, coc);
}
"""

# Horneado de figuras sueltas (público): el mismo litFighter del combate sobre un sprite con su mapa de
# altura y piezas. Devuelve color ya iluminado (sin el tinte global, que el shader de escena aplica a tBg).
BAKE = SCENE[:SCENE.index("void main() {\n    vec2 p = vec2(gl_FragCoord.x")] + """
uniform vec4 uHaze;            // perspectiva aérea: color del aire (lineal) y cuánto
void main() {
    vec2 p = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y);
    vec4 fg = Lp(tFig, p);
    if (fg.a < 0.004) { frag = vec4(0.); return; }
    vec3 c = litFighter(p, fg);
    // a seis metros el aire del dojo come contraste: los blancos no compiten con los luchadores
    // y les tiñe la sombra: a contraluz de los shōji la sombra fría del tejido viraba a verde
    c *= mix(vec3(1.), normalize(uHaze.rgb + 1e-4) * 1.732, uHaze.a * 2.0);
    c = mix(c, uHaze.rgb + c * 0.35, uHaze.a);
    frag = vec4(pow(clamp(c, 0., 1.), vec3(1. / 2.2)), fg.a);
}
"""

RAYS = COMMON + """
uniform sampler2D tBg, tFig, tPlate;
uniform vec2 uRes;
uniform vec2 uLightUV;
uniform float uFloorBackY, uDensity, uDecay, uTime, uPlateOn;
uniform vec4 uWall, uPlate;
float mask(vec2 q) {
    vec4 b = textureLod(tBg, q, 3.5 + LB());     // máscara desenfocada: sin moiré con la celosía
    if (uPlateOn > 0.5) {
        vec2 p = q * uRes;
        vec2 w = vec2((p.x - uWall.x) / uWall.z, (p.y - uWall.y) / uWall.z);
        b = vec4(textureLod(tPlate, (w - uPlate.xy) / uPlate.zw, 4.).rgb, 1.);
    }
    float a = textureLod(tFig, q, 2.0 + LB()).a;
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

# ── rasterizado de las figuras (punto 16) ─────────────────────────────────────
# Los triángulos llegan en píxeles de la imagen (y hacia abajo) en el orden de pintado; la fila 0 de la
# textura es la parte de arriba de la imagen, como en las capas que sube pygame.
FIG_VERT = """
#version 330
in vec2 in_pos;
in vec4 in_col;
uniform vec2 uRes;
out vec4 v_col;
void main() { v_col = in_col; gl_Position = vec4(in_pos / uRes * 2.0 - 1.0, 0.0, 1.0); }
"""
FIG_FRAG = """
#version 330
in vec4 v_col;
out vec4 frag;
uniform float uSwz;          // la capa destino se lee con swizzle BGRA (el orden de bytes de pygame)
void main() { frag = uSwz > 0.5 ? v_col.bgra : v_col; }
"""
# composición: la figura resuelta del MSAA (premultiplicada por la cobertura) sobre las estelas de pygame
FIG_OVER = COMMON + """
uniform sampler2D tSrc;
uniform float uSwz;
void main() {
    vec4 c = texelFetch(tSrc, ivec2(gl_FragCoord.xy), 0);
    if (c.a < 1e-4) discard;
    vec3 rgb = c.rgb / c.a;
    frag = vec4(uSwz > 0.5 ? rgb.bgr : rgb, c.a);
}
"""

# ── distancia al contorno (jump flood) ─────────────────────────────────────────
# Semillas: píxeles de tinta o vacíos (fuera de la figura). Cada texel acaba guardando la semilla más
# cercana; con ella el shader sabe a qué distancia y en qué dirección está el borde de su miembro y le
# da la normal de un cilindro de la anchura local real (brazo, pierna, torso, cabeza).
JFA_INIT = COMMON + """
uniform sampler2D tFig, tFigD;
uniform float uK;                 // píxeles de la imagen por texel del campo
void main() {
    ivec2 c = ivec2(gl_FragCoord.xy);
    ivec2 sz = textureSize(tFig, 0);
    bool seed = false;
    for (int j = 0; j < 2; j++) for (int i = 0; i < 2; i++) {
        ivec2 q = min(ivec2(vec2(c) * uK) + ivec2(i, j) * int(uK > 1.5), sz - 1);
        float a = texelFetch(tFig, q, 0).a;
        float id = texelFetch(tFigD, q, 0).g;
        seed = seed || a < 0.5 || id < 0.5 / 255.;
    }
    frag = vec4(seed ? gl_FragCoord.xy : vec2(-1e6), 0., 1.);
}
"""

JFA_STEP = COMMON + """
uniform sampler2D tSeed;
uniform int uStep;
void main() {
    ivec2 c = ivec2(gl_FragCoord.xy);
    ivec2 sz = textureSize(tSeed, 0);
    vec2 best = vec2(-1e6);
    float bd = 1e20;
    for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
        ivec2 q = c + ivec2(i, j) * uStep;
        if (q.x < 0 || q.y < 0 || q.x >= sz.x || q.y >= sz.y) continue;
        vec2 sd = texelFetch(tSeed, q, 0).xy;
        if (sd.x < -1e5) continue;
        vec2 d = sd - gl_FragCoord.xy;
        float dd = dot(d, d);
        if (dd < bd) { bd = dd; best = sd; }
    }
    frag = vec4(best, 0., 1.);
}
"""

# Pendiente de la forma por texel del campo: cilindro de la anchura local. d es la distancia al borde; la
# semianchura local se busca hacia dentro (el máximo de la distancia en la dirección contraria al borde:
# la cresta del miembro). En la cresta la normal mira a cámara; en el borde, de canto. Un contorno que
# pertenece a una capa de encima (el brazo sobre el pecho) apenas curva lo de debajo.
FORM = COMMON + """
uniform sampler2D tJfa, tFigD, tPieces;
uniform float uJfaK;
uniform vec2 uRes;
vec2 seedAt(vec2 p) { return texelFetch(tJfa, ivec2(p / uJfaK), 0).xy * uJfaK; }
float distAt(vec2 p) { vec2 s = seedAt(p); return s.x < -1e4 ? 0. : length(s - p); }
void main() {
    vec2 own = texelFetch(tJfa, ivec2(gl_FragCoord.xy), 0).xy;
    vec2 p = gl_FragCoord.xy * uJfaK;
    ivec2 ip = ivec2(min(p, uRes - 1.));
    vec4 fd = texelFetch(tFigD, ip, 0);
    int id = int(fd.g * 255. + 0.5);
    if (id == 0 || own.x < -1e4 || distance(own, gl_FragCoord.xy) < 0.01) { frag = vec4(0.); return; }
    float pxcm = max(texelFetch(tPieces, ivec2(id, 0), 0).w, 0.05);
    vec2 sp = own * uJfaK;
    vec2 dv = sp - p;
    float d = length(dv);
    vec2 o = dv / max(d, 1e-3);
    float cap = 24. * pxcm;
    float w = d, t = max(d, 1.) * 0.5;
    for (int i = 0; i < 8; i++) {
        if (t > cap * 2.2) break;
        float dq = distAt(p - o * t);
        w = max(w, dq);
        if (dq < w * 0.55) break;                       // pasada la cresta
        t *= 1.5;
    }
    float we = min(w, cap);
    float sn = clamp(1. - d / max(we, 1.), 0., 1.);
    float hprof = we * sqrt(1. - sn * sn) * 0.8;          // altura del perfil (px): para la autosombra
    float inkH = texelFetch(tFigD, ivec2(clamp(sp, vec2(0.), uRes - 1.)), 0).r;
    sn *= mix(1., 0.22, step(fd.r + 0.05, inkH + 34. / 255.));
    vec2 slope = vec2(sn * o.x, -sn * o.y) / max(sqrt(1. - sn * sn), 0.3);
    frag = vec4(slope, hprof, 1.);
}
"""

# Suavizado de la pendiente con máscara (sólo dentro de las figuras): bordes sin escalones.
FORM_BLUR = COMMON + """
uniform sampler2D tSrc;
uniform vec2 uDir;
void main() {
    ivec2 c = ivec2(gl_FragCoord.xy);
    ivec2 sz = textureSize(tSrc, 0);
    vec4 own = texelFetch(tSrc, c, 0);
    if (own.w < 1e-3) { frag = vec4(0.); return; }
    vec4 acc = vec4(0.);
    for (int i = -3; i <= 3; i++) {
        ivec2 q = clamp(c + ivec2(uDir * float(i)), ivec2(0), sz - 1);
        float wgt = 1. - abs(float(i)) / 4.;
        acc += texelFetch(tSrc, q, 0) * wgt;
    }
    frag = acc / max(acc.w, 1e-4);
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
        off += normalize(dv + 1e-4) * exp(-pow((r - w.z) / (16. * uRS), 2.)) * w.w * 12. * uRS;
    }
    p -= off;
    q = vec2(p.x / uRes.x, 1. - p.y / uRes.y);
    vec2 dir = q - 0.5;
    float ca = uCA * (0.0015 + 0.012 * dot(dir, dir));
    vec3 col = vec3(texture(tScene, q + dir * ca).r, texture(tScene, q).g, texture(tScene, q - dir * ca).b);
    col += texture(tRays, q).rgb * uRaysCol * uRaysI;
    col = mix(col, texture(tBloom, q).rgb, uBloom);
    if (uSpot > 0.) {
        float hw = (60. + clamp(p.y / uSpotPos.y, 0., 1.3) * 250.) * uRS;
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
    float n = hash12(floor(gl_FragCoord.xy / uRS) + fract(uTime * 7.31) * vec2(91.7, 37.3)) - 0.5;   // grano de película del mismo tamaño a cualquier resolución
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


def figure_light(rig: dict, g: dict) -> dict:
    """La luz que reciben las figuras con una luz de asalto (la de escena y la del horneado del público)."""
    return dict(key=_lin(rig["key"], g["key_i"]), amb=_lin(rig["ambient"], g["amb_i"]), key3=g["key3"],
                sky=_lin(mixc(rig["ambient"], rig["sky_bot"], 0.4), g["amb_i"] * 1.5),
                bounce=_lin(rig["floor"], 0.35 * g["amb_i"]),
                lan=(1.0 * rig["lanterns"], 0.5 * rig["lanterns"], 0.2 * rig["lanterns"]),
                lan_i=rig["lanterns"], rim=g["rim"])


def scene_params(dojo, cam, fighters, fx, cfg, extra=None) -> dict:
    """Recoge todo lo que la GPU necesita de un fotograma: cámara, luz, luchadores, decals, ondas."""
    rig = dojo.rig
    g = dojo.gpu_rig
    zoom = cam.zoom
    base_y = FLOOR_Y + cam.yoff + cam.shake_y + cam_base_off(zoom)
    depth = 190 * (0.75 + zoom * 0.25) * RS
    lanterns = []
    for (sx, sy, inten) in getattr(dojo, "lantern_pos", [])[:4]:
        k = 1 - 0.42 * 0.9
        wx = (sx - W / 2 - cam.shake_x) / (zoom * 0.95 * k * RS) + cam.x
        lanterns.append((sx, sy, wx, inten))
    while len(lanterns) < 4:
        lanterns.append((0.0, 0.0, 0.0, 0.0))
    figs = []
    for f in fighters[:2]:
        sx, _ = cam.P(f.x, 0)
        figs.append((sx, base_y, cam.S(max(0.0, -f.y)), cam.S(46 * f.width * f.scale)))
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
        figure_light(rig, g),
        cam=(cam.x, zoom * RS, base_y, cam.shake_x), depth=depth, floor_back=base_y - depth,
        tint=_lin(rig["tint"]), beam_i=g["beam_i"] if cfg.get("beams", True) else 0.0,
        refl=g["refl"], wet=g["wet"], emissive=g["emissive"], sky_fill=g["sky_fill"],
        light_dir=g["light_dir"], shadow=g["shadow"] if cfg.get("shadows", True) else (0, 1, 0, 0),
        lanterns=lanterns, fighters=figs, decals=decals, waves=waves, tex=float(cfg.get("tex", 1.0)),
        wall=dojo.wall_geom(cam), plate=getattr(dojo, "plate", None), plate_from=getattr(dojo, "plate_from", None),
        plate_mix=float(getattr(dojo, "plate_mix", 1.0)), pine_sway=float(getattr(dojo, "wind", 0.0)) * 14.0,
        plate_next=getattr(dojo, "plate_next", None),
        light_uv=g["light_uv"], rays_i=g["rays"] if cfg.get("beams", True) else 0.0, rays_col=g["rays_col"],
        exposure=g["exposure"], lift=g["lift"], gain=g["gain"], sat=g["sat"], power=g["power"],
        bloom=0.05 if cfg.get("bloom") else 0.02, ca=0.25 + (fx.ca if fx is not None else 0.0),
        vig=g["vig"], grain=0.03 if cfg.get("grain") else 0.0,
        spot=dojo.single_beam, spot_pos=(W / 2, FLOOR_Y + 40 * RS),
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
    BLOOM_LEVELS = 6 + int(round(math.log2(RS)))

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
        self.p_bake, self.v_bake = prog(BAKE)
        self.p_jinit, self.v_jinit = prog(JFA_INIT)
        self.p_jstep, self.v_jstep = prog(JFA_STEP)
        self.p_form, self.v_form = prog(FORM)
        self.p_fblur, self.v_fblur = prog(FORM_BLUR)
        self.p_fover, self.v_fover = prog(FIG_OVER)
        self.p_fig = ctx.program(vertex_shader=FIG_VERT, fragment_shader=FIG_FRAG)
        self._figr = None                  # objetivos del rasterizado de figuras (se crean al primer uso)

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
        self.t_pieces = ctx.texture((256, 2), 4, dtype="f4")        # tabla de piezas: ancla, eje, material, tejido
        self.t_pieces.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.t_bake_pieces = ctx.texture((256, 2), 4, dtype="f4")   # la del horneado, aparte de la del fotograma
        self.t_bake_pieces.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self._pieces = np.zeros((2, 256, 4), dtype="f4")
        self.slots = [dict(ver=None, col=None, mat=None), dict(ver=None, col=None, mat=None)]   # lámina actual y anterior
        self._dummy = tex((4, 4))
        self._figd_blank = None
        self._tex = tex
        self.t_hud = tex((W, H))            # la interfaz a resolución de render (ui.UISurface): texto nítido
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
        self.jfa = self._jfa_targets((max(1, W // 2), max(1, H // 2)))

    def _fig_targets(self):
        """MSAA para el color de las figuras, su resolución y framebuffers sobre las capas de figura y datos."""
        if self._figr is None:
            ctx = self.ctx
            samples = 0
            for n in (8, 4, 2):
                if n <= getattr(ctx, "max_samples", 0):
                    samples = n
                    break
            rb = ctx.renderbuffer((W, H), 4, samples=samples) if samples else None
            res = self._tex((W, H))
            self._figr = dict(samples=samples, msaa=ctx.framebuffer([rb]) if rb else None, rb=rb, res=res,
                              res_fb=ctx.framebuffer([res]), fig_fb=ctx.framebuffer([self.t_fig]),
                              figd_fb=ctx.framebuffer([self.t_figd]))
        return self._figr

    def _raster_figures(self, batch, rect):
        """Pinta en la GPU los luchadores grabados: el color con MSAA, compuesto sobre las estelas que ya trae
        la capa de figura; el mapa de datos sin suavizado (los números de pieza no se pueden mezclar)."""
        ctx = self.ctx
        R = self._fig_targets()
        pr = self.p_fig
        self._set(pr, "uRes", (float(W), float(H)))
        sc = None
        if rect is not None:
            x, y, w, h = rect
            x0, y0 = max(0, int(x) - 2), max(0, int(y) - 2)
            sc = (x0, y0, max(1, min(W, int(x + w) + 2) - x0), max(1, min(H, int(y + h) + 2) - y0))

        def draw(blocks, fb, swz):
            blocks = [b for b in blocks if len(b)]
            if not blocks:
                return
            self._set(pr, "uSwz", 1.0 if swz else 0.0)
            buf = ctx.buffer(np.concatenate(blocks).astype("f4").tobytes())
            va = ctx.vertex_array(pr, [(buf, "2f 4f", "in_pos", "in_col")])
            fb.use()
            va.render(moderngl.TRIANGLES)
            va.release()
            buf.release()

        ctx.disable(moderngl.BLEND)
        # datos: directos sobre la capa (vacía) de datos
        R["figd_fb"].scissor = None
        R["figd_fb"].clear(0.0, 0.0, 0.0, 0.0)
        draw(batch["data"], R["figd_fb"], self.t_figd.swizzle == "BGRA")
        # color: MSAA → resolución → encima de la capa de figura con alfa recto
        tgt = R["msaa"] or R["res_fb"]
        tgt.scissor = sc
        tgt.clear(0.0, 0.0, 0.0, 0.0)
        draw(batch["albedo"], tgt, False)
        tgt.scissor = None
        if R["msaa"] is not None:
            ctx.copy_framebuffer(R["res_fb"], R["msaa"])
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA, moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)
        pr2 = self.p_fover
        self._bind(pr2, tSrc=R["res"])
        self._set(pr2, "uSwz", 1.0 if self.t_fig.swizzle == "BGRA" else 0.0)
        R["fig_fb"].scissor = sc
        R["fig_fb"].use()
        self.v_fover.render(moderngl.TRIANGLES, vertices=3)
        R["fig_fb"].scissor = None
        ctx.blend_func = moderngl.DEFAULT_BLENDING
        ctx.disable(moderngl.BLEND)
        self.t_fig.build_mipmaps()
        self.t_figd.build_mipmaps()

    def _jfa_targets(self, size):
        """Dos campos de semillas (ida y vuelta del jump flood) y dos de pendiente (forma y su suavizado)."""
        out = []
        for comps, dt, filt in ((2, "f4", moderngl.NEAREST), (2, "f4", moderngl.NEAREST),
                                (4, "f2", moderngl.LINEAR), (4, "f2", moderngl.LINEAR)):
            t = self.ctx.texture(size, comps, dtype=dt)
            t.filter = (filt, filt)
            t.repeat_x = t.repeat_y = False
            out.append((t, self.ctx.framebuffer([t])))
        return out

    def _form(self, t_fig, t_figd, t_pieces, targets, k, res, rect=None):
        """Pendiente de la forma de las figuras, suavizada: la normal de volumen de cada miembro.
        Con `rect` (zona de las figuras en la imagen) todo el trabajo se acota a ella con un margen."""
        (tf, ff), (tg, fg) = targets[2], targets[3]
        for _t, f in targets[:2]:
            f.clear(-1e6, -1e6, 0.0, 1.0)
        for f in (ff, fg):
            f.clear(0.0, 0.0, 0.0, 0.0)
        if rect is not None:
            w, h = tf.size
            pad = 40
            x0, y0 = max(0, int(rect[0] / k) - pad), max(0, int(rect[1] / k) - pad)
            x1, y1 = min(w, int((rect[0] + rect[2]) / k) + pad), min(h, int((rect[1] + rect[3]) / k) + pad)
            for _t, f in targets:
                f.scissor = (x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        t_seed = self._jfa(t_fig, t_figd, targets[:2], k)
        pr = self.p_form
        self._bind(pr, tJfa=t_seed, tFigD=t_figd, tPieces=t_pieces)
        self._set(pr, "uJfaK", float(k))
        self._set(pr, "uRes", (float(res[0]), float(res[1])))
        ff.use()
        self.v_form.render(moderngl.TRIANGLES, vertices=3)
        pr = self.p_fblur
        for src, dst, d in ((tf, fg, (1.0, 0.0)), (tg, ff, (0.0, 1.0))):
            self._bind(pr, tSrc=src)
            self._set(pr, "uDir", d)
            dst.use()
            self.v_fblur.render(moderngl.TRIANGLES, vertices=3)
        for _t, f in targets:
            f.scissor = None
        return tf

    def _jfa(self, t_fig, t_figd, targets, k):
        """Campo de distancias al contorno de las figuras (jump flood). Devuelve la textura final."""
        (ta, fa), (tb, fb) = targets[:2]
        w, h = ta.size
        pr = self.p_jinit
        self._bind(pr, tFig=t_fig, tFigD=t_figd)
        self._set(pr, "uK", float(k))
        fa.use()
        self.v_jinit.render(moderngl.TRIANGLES, vertices=3)
        step = 1
        while step * 2 < max(w, h) and step < 64:
            step *= 2
        src, dst = (ta, fa), (tb, fb)
        steps = []
        while step >= 1:
            steps.append(step)
            step //= 2
        steps.append(1)                                    # pasada extra: corrige los errores del JFA
        pr = self.p_jstep
        for st in steps:
            self._bind(pr, tSeed=src[0])
            self._set(pr, "uStep", st)
            dst[1].use()
            self.v_jstep.render(moderngl.TRIANGLES, vertices=3)
            src, dst = dst, src
        return src[0]

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
    def _upload(self, tex, surf: pygame.Surface, mip=False, rect=None):
        if self._swz is None:
            masks = surf.get_masks()
            self._swz = "BGRA" if masks[0] == 0xFF0000 else "RGBA"
        if rect is not None and tex.swizzle == self._swz:
            # sólo la zona que cambió: los mismos bytes que la vista completa, en el mismo orden de canales
            sub = surf.subsurface(rect)
            tex.write(pygame.image.tobytes(sub, self._swz), viewport=(rect.x, rect.y, rect.w, rect.h))
            if mip:
                tex.build_mipmaps()
            return
        tex.swizzle = self._swz
        try:
            tex.write(surf.get_view("1"))
        except Exception:
            tex.swizzle = "RGBA"
            tex.write(pygame.image.tobytes(surf, "RGBA"))
        if mip:
            tex.build_mipmaps()

    def _upload_hud(self, hud):
        """La interfaz: si es una UISurface, sólo las franjas pintadas en este fotograma o en el anterior."""
        surf = getattr(hud, "surf", hud)
        rects = hud.take_dirty() if hasattr(hud, "take_dirty") else None
        if self.t_hud.size != surf.get_size():
            self.t_hud.release()
            self.t_hud = self._tex(surf.get_size())
            rects = None
        if rects is None or self.t_hud.swizzle != self._swz:
            self._upload(self.t_hud, surf)
            return
        for r in rects:
            self._upload(self.t_hud, surf, rect=r)

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
        self._set(prog, "uRS", float(RS))

    def _slot(self, plate, keep=None):
        """Textura de una lámina de pared (color + máscara) con mipmaps y filtrado anisótropo. Hay dos
        huecos (actual y anterior, para el fundido entre asaltos); sólo se sube lo que es nuevo."""
        if plate is None:
            return None
        for sl in self.slots:
            if sl["ver"] == plate["version"]:
                return sl
        keep_v = keep["version"] if keep is not None else None
        sl = next((s_ for s_ in self.slots if s_["ver"] != keep_v), self.slots[0])
        size = plate["col"].get_size()
        for name, src in (("col", plate["col"]), ("mat", plate["mat"])):
            t = sl[name]
            if t is None or t.size != size:
                if t is not None:
                    t.release()
                t = self.ctx.texture(size, 4, dtype="f1")
                t.repeat_x = t.repeat_y = False
                t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                try:
                    t.anisotropy = 8.0
                except Exception:
                    pass
                sl[name] = t
            t.swizzle = "RGBA"
            t.write(pygame.image.tobytes(src, "RGBA"))
            t.build_mipmaps()
        sl["ver"] = plate["version"]
        return sl

    def _wall_uniforms(self, pr, p, on):
        S = self._set
        S(pr, "uPlateOn", 1.0 if on else 0.0)
        if on:
            x0, wt, fb, bgz = p["wall"]
            S(pr, "uWall", (float(x0), float(wt), float(bgz), float(fb)))
            S(pr, "uPlate", tuple(float(v) for v in p["plate"]["rect"]))
            S(pr, "uPineSway", float(p.get("pine_sway", 0.0)))

    # ----- fotograma -----
    profile = False                     # tools/bench.py: tiempo de cada etapa (sincroniza la GPU entre etapas)

    def _mark(self, name):
        if not self.profile:
            return
        import time
        self.ctx.finish()
        now = time.perf_counter()
        if name is not None and getattr(self, "_t_prev", None) is not None:
            self.timings[name] = self.timings.get(name, 0.0) + (now - self._t_prev) * 1e3
        self._t_prev = now

    def render(self, p: dict, bg, fig, fx, hud, dt=1 / 60, window_size=None, figd=None):
        ctx = self.ctx
        if self.profile:
            self.timings = getattr(self, "timings", {})
            self._t_prev = None
            self._mark(None)
        self.t += dt
        dirty = p.get("dirty", "full")
        if dirty == "full" or dirty is None:
            self._upload(self.t_bg, bg, mip=True)
            self._upload(self.t_fig, fig, mip=True)
        else:
            if dirty.get("bg") is not None:
                self._upload(self.t_bg, bg, mip=True, rect=dirty["bg"])
            if dirty.get("fig") is not None:
                self._upload(self.t_fig, fig, mip=True, rect=dirty["fig"])
        plate, pfrom = p.get("plate"), p.get("plate_from")
        cur = self._slot(plate, keep=pfrom)
        old = self._slot(pfrom, keep=plate) if pfrom is not None and p.get("plate_mix", 1.0) < 0.999 else None
        if old is None and p.get("plate_next") is not None and cur is not None:
            self._slot(p["plate_next"], keep=plate)            # la del asalto siguiente, subida por adelantado
        plate_on = cur is not None
        self.t_plate, self.t_platem = (cur["col"], cur["mat"]) if cur else (None, None)
        self.t_plate2 = old["col"] if old else self.t_plate
        self._mark("subidas: fondo y figuras")
        batch = p.get("fig_batch")
        if batch is not None:
            self._raster_figures(batch, p.get("fig_rect"))
            self._mark("rasterizado de figuras")
        if figd is not None and batch is None:
            if dirty == "full" or dirty is None:
                self._upload(self.t_figd, figd, mip=True)
            elif dirty.get("fig") is not None:
                self._upload(self.t_figd, figd, mip=True, rect=dirty["fig"])
        if figd is not None:
            pcs = p.get("pieces") or []
            arr = self._pieces
            arr[:] = 0.0
            for i, pc in enumerate(pcs[:254]):
                arr[0, i + 1] = pc[0:4]
                arr[1, i + 1] = pc[4:8]
            self.t_pieces.write(arr.tobytes())
        self._upload(self.t_fx, fx)
        self._upload_hud(hud)
        self._mark("subidas: datos, efectos e interfaz")
        S = self._set
        ctx.disable(moderngl.BLEND)

        # 1) escena HDR (con el campo de distancias de las figuras para su volumen)
        t_form = self._form(self.t_fig, self.t_figd, self.t_pieces, self.jfa, 2, (W, H), p.get("fig_rect")) \
            if figd is not None else self._dummy
        ctx.disable(moderngl.BLEND)
        pr = self.p_scene
        self._bind(pr, tBg=self.t_bg, tFig=self.t_fig, tFx=self.t_fx, tFigD=self.t_figd, tPieces=self.t_pieces, tForm=t_form,
                   tPlate=self.t_plate if plate_on else self._dummy, tPlateM=self.t_platem if plate_on else self._dummy,
                   tPlate2=self.t_plate2 if plate_on else self._dummy)
        self._wall_uniforms(pr, p, plate_on)
        S(pr, "uPlateMix", float(p.get("plate_mix", 1.0)) if plate_on and self.t_plate2 is not self.t_plate else 1.0)
        S(pr, "uRes", (float(W), float(H)))
        S(pr, "uTime", self.t)
        S(pr, "uFigLit", 1.0 if figd is not None else 0.0)
        S(pr, "uDimBg", float(p.get("dim_bg", 0.0)))
        S(pr, "uTexI", float(p.get("tex", 1.0)))
        S(pr, "uDebugN", float(os.environ.get("TATAMIDO_DEBUG_N", "0")))
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

        self._mark("forma (JFA) y escena")
        # 2) profundidad de campo
        if p.get("dof", 0) > 0.3:
            pr = self.p_dof
            self._bind(pr, tScene=self.t_scene)
            S(pr, "uTexel", (1 / W, 1 / H))
            S(pr, "uDof", float(p["dof"]) * RS)
            self.f_dof.use()
            self.v_dof.render(moderngl.TRIANGLES, vertices=3)
            src = self.t_dof

        self._mark("profundidad de campo")
        # 3) rayos volumétricos (media resolución)
        pr = self.p_rays
        self._bind(pr, tBg=self.t_bg, tFig=self.t_fig, tPlate=self.t_plate if plate_on else self._dummy)
        self._wall_uniforms(pr, p, plate_on)
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

        self._mark("rayos")
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

        self._mark("bloom")
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

        self._mark("composición final")
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
        self._mark("FXAA e interfaz")

    def bake_figure(self, img: pygame.Surface, data: pygame.Surface, pieces, light: dict, feet, tex=1.0,
                    lantern=None, haze=None) -> pygame.Surface:
        """Ilumina una figura suelta (albedo + altura/piezas de render_figure) con la luz de los luchadores y la
        devuelve como sprite RGBA: tejidos, rampa pintada, brillos de material, contraluz y farolillo.
        `feet` es el punto de apoyo en el sprite; `lantern`, un farolillo (x, y en px del sprite, intensidad);
        `haze`, el aire entre la figura y la cámara (r, g, b lineales y cantidad)."""
        ctx = self.ctx
        size = img.get_size()

        def up(surf):
            t = ctx.texture(size, 4, pygame.image.tobytes(surf, "RGBA"))
            t.repeat_x = t.repeat_y = False
            t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            t.build_mipmaps()
            return t

        t_fig, t_figd = up(img), up(data)
        arr = np.zeros((2, 256, 4), dtype="f4")
        for i, pc in enumerate(pieces[:254]):
            arr[0, i + 1] = pc[0:4]
            arr[1, i + 1] = pc[4:8]
        self.t_bake_pieces.write(arr.tobytes())
        jt = self._jfa_targets(size)
        t_form = self._form(t_fig, t_figd, self.t_bake_pieces, jt, 1, size)
        out = ctx.texture(size, 4)
        fbo = ctx.framebuffer([out])
        pr = self.p_bake
        self._bind(pr, tFig=t_fig, tFigD=t_figd, tPieces=self.t_bake_pieces, tForm=t_form)
        S = self._set
        S(pr, "uRes", (float(size[0]), float(size[1])))
        S(pr, "uTexI", float(tex))
        for k, n in (("key3", "uKey3"), ("sky", "uSky"), ("bounce", "uBounce"), ("key", "uKey"), ("lan", "uLan")):
            S(pr, n, tuple(float(v) for v in light[k]))
        S(pr, "uTint", (1.0, 1.0, 1.0))
        S(pr, "uRimI", float(light["rim"]))
        S(pr, "uLanI", float(light["lan_i"]))
        lan = [(0.0, 0.0, 0.0, 0.0)] * 4
        if lantern is not None and lantern[2] > 0.0:
            lan[0] = (float(lantern[0]), float(lantern[1]), 0.0, float(lantern[2]))
        S(pr, "uLanterns", lan)
        S(pr, "uHaze", tuple(float(v) for v in haze) if haze else (0.0, 0.0, 0.0, 0.0))
        fx, fy = feet
        S(pr, "uFighters", [(float(fx), float(fy), 0.0, float(size[0])), (float(fx), float(fy), 0.0, float(size[0]))])
        ctx.disable(moderngl.BLEND)
        fbo.use()
        fbo.clear(0.0, 0.0, 0.0, 0.0)
        self.v_bake.render(moderngl.TRIANGLES, vertices=3)
        # al formato nativo de pygame: un sprite RGBA se convertiría píxel a píxel en cada blit (20× más lento)
        res = pygame.image.frombytes(fbo.read(components=4), size, "RGBA", True).convert(
            pygame.Surface((1, 1), pygame.SRCALPHA))
        for o in (fbo, out, t_fig, t_figd) + tuple(x for pair in jt for x in pair):
            o.release()
        return res

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
