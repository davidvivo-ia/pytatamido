"""Cómo son los doce luchadores: tipo de cuerpo, rostro, pelo y vestuario.

Todo está en centímetros, en el espacio local de la pose (mirando a la derecha, y negativa hacia
arriba, origen en el suelo bajo la cadera). `render_fighter` convierte esto en píxeles.
"""
from __future__ import annotations

# Muestras a lo largo de la columna: -0.1 entrepierna/glúteo, 0 cadera, 1 base del cuello.
SPINE_T = [-0.1, 0.0, 0.12, 0.28, 0.45, 0.62, 0.78, 0.9, 1.0]

# Perfil del torso: distancia a la columna por delante (pecho, vientre) y por detrás (espalda, glúteo).
# Miembros: (radio inicial, radio final, abultamiento muscular).
BODIES = {
    "atleta": dict(front=[6.0, 8.0, 10.0, 8.0, 8.5, 11.0, 10.5, 8.0, 5.5],
                   back=[10.5, 11.0, 10.0, 8.0, 9.0, 10.5, 11.5, 10.0, 6.0],
                   thigh=(8.2, 5.4, 1.7), calf=(5.0, 3.0, 1.6), upper=(4.9, 3.6, 1.2), fore=(4.1, 2.5, 1.0),
                   neck=5.6, head=11.0, hand=4.3, delt=5.6),
    "fibroso": dict(front=[5.5, 7.5, 9.0, 7.0, 7.5, 10.0, 9.5, 7.5, 5.0],
                    back=[9.5, 10.0, 9.0, 7.0, 8.0, 9.5, 10.5, 9.0, 5.5],
                    thigh=(7.4, 4.8, 1.6), calf=(4.6, 2.7, 1.6), upper=(4.4, 3.2, 1.2), fore=(3.8, 2.3, 1.0),
                    neck=5.0, head=10.8, hand=4.0, delt=5.0),
    "atleta_f": dict(front=[6.0, 8.5, 10.0, 7.5, 7.8, 10.6, 10.0, 7.5, 5.0],
                     back=[11.5, 12.0, 10.5, 7.5, 8.2, 9.5, 10.5, 9.0, 5.4],
                     thigh=(8.0, 5.0, 1.5), calf=(4.6, 2.7, 1.5), upper=(4.2, 3.1, 0.9), fore=(3.6, 2.2, 0.8),
                     neck=4.8, head=10.5, hand=3.8, delt=4.8),
    "menuda": dict(front=[5.5, 7.5, 8.8, 6.8, 7.0, 9.6, 9.0, 7.0, 4.7],
                   back=[10.0, 10.5, 9.0, 6.8, 7.4, 8.6, 9.6, 8.4, 5.0],
                   thigh=(7.0, 4.5, 1.2), calf=(4.2, 2.5, 1.2), upper=(3.8, 2.8, 0.7), fore=(3.3, 2.0, 0.6),
                   neck=4.5, head=10.4, hand=3.5, delt=4.4),
    "pesado": dict(front=[7.5, 10.0, 12.0, 12.0, 12.0, 12.5, 11.0, 9.0, 6.5],
                   back=[12.0, 12.0, 11.5, 10.5, 11.0, 12.0, 13.0, 11.0, 7.0],
                   thigh=(9.6, 6.2, 1.8), calf=(5.8, 3.4, 1.8), upper=(5.8, 4.3, 1.4), fore=(4.8, 3.0, 1.2),
                   neck=7.0, head=11.6, hand=4.8, delt=6.6),
    "sumo": dict(front=[10.0, 18.0, 26.0, 30.0, 28.0, 21.0, 15.0, 11.0, 8.0],
                 back=[15.0, 16.0, 17.0, 15.0, 14.0, 14.0, 14.0, 12.0, 8.0],
                 thigh=(13.0, 8.0, 1.5), calf=(7.6, 4.5, 1.6), upper=(7.6, 6.0, 1.0), fore=(6.2, 4.2, 0.8),
                 neck=8.5, head=12.4, hand=5.4, delt=8.5),
    "anciano": dict(front=[5.5, 7.5, 9.0, 7.5, 7.5, 8.8, 8.6, 7.5, 5.0],
                    back=[9.5, 10.0, 9.0, 7.8, 8.5, 9.8, 11.0, 10.2, 5.6],
                    thigh=(6.8, 4.4, 0.8), calf=(4.2, 2.5, 0.8), upper=(3.8, 2.9, 0.5), fore=(3.4, 2.1, 0.4),
                    neck=4.6, head=10.6, hand=3.7, delt=4.4),
}

# Rostro y pelo:
#   nose: recta | ancha | rota | fina · jaw: fuerza de la mandíbula · brow: grosor de la ceja
#   hair: estilo de peinado · extras: rasgos visibles
# Vestuario:
#   top: gi | kurtka | changshan | tank | singlet | keikogi | none
#   bottom: gi | shorts_sambo | abada | shorts_thai | singlet | track | hakama | mawashi
LOOKS = {
    "haruki": dict(guard_open=True, body="atleta", skin="#C99A6E", hair_c="#161414", hair="corto", nose="recta", jaw=1.15, brow=1.2,
                   extras=["coliflor"], top="gi", bottom="gi", cloth="#F2EFE6", collar="#E6D9B0", belt="#151515",
                   sleeve=0.82, feet="descalzo", belt_style="deshilachado", expression="contenida"),
    "ines": dict(body="atleta_f", skin="#D8A47F", hair_c="#121010", hair="trenza", nose="fina", jaw=0.95, brow=1.0,
                 extras=["cicatriz", "bordado"], top="gi", bottom="gi", cloth="#FFFFFF", collar="#F2F2F2", belt="#111111",
                 accent="#7A1E1E", sleeve=0.4, feet="descalzo", expression="feroz"),
    "tomasz": dict(guard_open=True, body="pesado", skin="#E5BFA0", hair_c="#8C6A3F", hair="rapado", nose="ancha", jaw=1.25, brow=1.4,
                   extras=["barba", "rubor"], top="kurtka", bottom="shorts_sambo", cloth="#B22222", collar="#B22222",
                   belt="#B22222", shorts="#1F3A93", sleeve=0.86, feet="botas", boots="#5A3E26", expression="seria"),
    "ayo": dict(body="fibroso", skin="#5C3A21", hair_c="#1A1410", hair="rastas", nose="ancha", jaw=1.0, brow=1.0,
                extras=["definido"], top="none", bottom="abada", cloth="#F7F7F2", cord=("#E3B505", "#2E8B57"),
                feet="descalzo", expression="sonrisa"),
    "kanoa": dict(guard_open=True, body="sumo", skin="#A86B43", hair_c="#0B0B0B", hair="chonmage", nose="ancha", jaw=1.3, brow=1.3,
                  extras=["papada"], top="none", bottom="mawashi", cloth="#0F2A44", feet="descalzo", expression="calma"),
    "lin": dict(guard_open=True, body="menuda", skin="#EBC9A8", hair_c="#141212", hair="melena", nose="fina", jaw=0.9, brow=0.8,
                extras=[], top="changshan", bottom="gi", cloth="#1B1B1B", collar="#1B1B1B", accent="#9E2A2B",
                pants="#6E6E6E", sleeve=0.97, feet="descalzo", expression="serena"),
    "tito": dict(guard_open=True, body="atleta", skin="#C68B59", hair_c="#3A2A1A", hair="rizado", nose="recta", jaw=1.05, brow=1.1,
                 extras=["coliflor", "parches"], top="gi", bottom="gi", cloth="#2C5AA0", collar="#244C8A", belt="#111111",
                 accent="#F2F2F2", sleeve=0.88, feet="descalzo", belt_style="grados", expression="sonrisa"),
    "somchai": dict(body="fibroso", skin="#B07D4F", hair_c="#111111", hair="corto", nose="ancha", jaw=1.1, brow=1.1,
                    extras=["sak_yant", "mongkol", "prajied", "definido"], top="none", bottom="shorts_thai",
                    cloth="#C1121F", accent="#FDF0D5", band=("#FDF0D5", "#003049"), feet="descalzo", expression="seria"),
    "yeon": dict(body="atleta_f", skin="#F1D3B3", hair_c="#2A1F1A", hair="coleta", nose="fina", jaw=0.9, brow=0.9,
                 extras=[], top="gi", bottom="gi", cloth="#FFFFFF", collar="#0C0C0C", belt="#111111", sleeve=0.86,
                 feet="descalzo", expression="concentrada"),
    "elsa": dict(guard_open=True, body="atleta_f", skin="#F3D9C7", hair_c="#EDE6C8", hair="trenza_nuca", nose="recta", jaw=1.05, brow=0.8,
                 extras=["pecas"], top="singlet", bottom="singlet", cloth="#5A6470", accent="#F2C14E", feet="botas_altas",
                 boots="#3A3F47", expression="sonrisa"),
    "matias": dict(body="atleta", skin="#D2A07A", hair_c="#141414", hair="corto", nose="rota", jaw=1.1, brow=1.2,
                   extras=["bigote", "ojeras", "guantes"], top="tank", bottom="track", cloth="#FFFFFF", pants="#75AADB",
                   feet="zapatillas", expression="picara"),
    "okabe": dict(guard_open=True, body="anciano", skin="#D9B99B", hair_c="#E8E8E8", hair="mono", nose="recta", jaw=0.95, brow=1.0,
                  extras=["barba_rala", "arrugas", "manchas"], top="keikogi", bottom="hakama", cloth="#F5F5F0",
                  collar="#ECEBE4", hakama="#101010", sleeve=0.72, feet="descalzo", expression="serena"),
}

# Público del dojo: alumnos y un maestro sentados en seiza junto a la pared.
_BASE = dict(nose="recta", jaw=1.0, brow=1.0, extras=[], feet="descalzo", expression="neutra", sleeve=0.85)
AUDIENCE = {
    "alumno_judo": dict(_BASE, body="atleta", skin="#C9A07A", hair_c="#181414", hair="corto", top="gi", bottom="gi",
                        cloth="#F2EFE6", collar="#E6DCC0", belt="#1A1A1A"),
    "alumna_judo": dict(_BASE, body="atleta_f", skin="#E8C4A2", hair_c="#2A1C14", hair="trenza", nose="fina", top="gi",
                        bottom="gi", cloth="#F5F3EC", collar="#E8E2D2", belt="#6B4423"),
    "alumno_azul": dict(_BASE, body="atleta", skin="#8D5A3B", hair_c="#1A1210", hair="rizado", top="gi", bottom="gi",
                        cloth="#2C5AA0", collar="#244C8A", belt="#5B3A8C"),
    "maestro": dict(_BASE, body="anciano", skin="#D9B99B", hair_c="#D8D8D4", hair="mono", top="keikogi", bottom="hakama",
                    cloth="#F5F5F0", collar="#ECEBE4", hakama="#18181C", extras=["arrugas"], sleeve=0.72),
    "alumna_karate": dict(_BASE, body="menuda", skin="#F0D0B0", hair_c="#121010", hair="melena", nose="fina", top="gi",
                          bottom="gi", cloth="#FFFFFF", collar="#F2F2F2", belt="#7A1E1E"),
    "alumno_mayor": dict(_BASE, body="pesado", skin="#E2BC9C", hair_c="#6E5A44", hair="rapado", nose="ancha", jaw=1.2,
                         top="gi", bottom="gi", cloth="#EFEDE6", collar="#E2DCCB", belt="#1A1A1A", extras=["barba"]),
}
LOOKS.update(AUDIENCE)
