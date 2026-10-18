import random

import pygame

from .config import *
from .dojo import Dojo

# ─────────────────────────────────────────────────────────────────────────────
#  POSPROCESADO (grano, LUTs del Modo Foto, filtros de accesibilidad)
# ─────────────────────────────────────────────────────────────────────────────
def apply_lut(surf, lut: str, dojo: Dojo, grain_on=True, contrast_access=False, daltonism="off"):
    if lut == "Caravaggio":
        m = pygame.Surface((W, H)); m.fill((255, 215, 170)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((40, 25, 15)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        surf.blit(dojo.vignette(255), (0, 0))
    elif lut == "Hammershøi":
        g = pygame.transform.grayscale(surf) if hasattr(pygame.transform, "grayscale") else surf.copy()
        g.set_alpha(140)
        surf.blit(g, (0, 0))
        m = pygame.Surface((W, H)); m.fill((215, 225, 240)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((18, 18, 22)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_ADD)
    elif lut == "Kurosawa B/N":
        g = pygame.transform.grayscale(surf) if hasattr(pygame.transform, "grayscale") else surf
        surf.blit(g, (0, 0))
        m = pygame.Surface((W, H)); m.fill((30, 30, 30)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        m.fill((235, 235, 235)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((255, 255, 255)); m2 = surf.copy(); m2.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)
        m.fill((160, 160, 160)); m2.blit(m, (0, 0), special_flags=pygame.BLEND_MULT); surf.blit(m2, (0, 0), special_flags=pygame.BLEND_ADD)
    elif lut == "Daido Moriyama":
        g = pygame.transform.grayscale(surf) if hasattr(pygame.transform, "grayscale") else surf
        surf.blit(g, (0, 0))
        m = pygame.Surface((W, H)); m.fill((45, 45, 45)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        g2 = surf.copy(); surf.blit(g2, (0, 0), special_flags=pygame.BLEND_ADD)
        if dojo.grain:
            for _ in range(3):
                g1, g2 = random.sample(dojo.grain, 2)
                surf.blit(g1, (0, 0), special_flags=pygame.BLEND_ADD)
                surf.blit(g2, (0, 0), special_flags=pygame.BLEND_SUB)
    if grain_on and dojo.grain and lut != "Daido Moriyama":
        g1, g2 = random.sample(dojo.grain, 2)
        surf.blit(g1, (0, 0), special_flags=pygame.BLEND_ADD)
        surf.blit(g2, (0, 0), special_flags=pygame.BLEND_SUB)
    if contrast_access:
        m = pygame.Surface((W, H)); m.fill((25, 25, 25)); surf.blit(m, (0, 0), special_flags=pygame.BLEND_SUB)
        c = surf.copy(); m.fill((70, 70, 70)); c.blit(m, (0, 0), special_flags=pygame.BLEND_MULT); surf.blit(c, (0, 0), special_flags=pygame.BLEND_ADD)
    if daltonism != "off":
        # filtro sencillo: desplaza los canales para separar rojo/verde
        m = pygame.Surface((W, H))
        m.fill({"protanopia": (255, 230, 255), "deuteranopia": (230, 255, 255), "tritanopia": (255, 255, 220)}.get(daltonism, (255, 255, 255)))
        surf.blit(m, (0, 0), special_flags=pygame.BLEND_MULT)


def bloom(surf, strength=0.5):
    """Bloom barato: reducimos, umbralizamos, ampliamos y sumamos."""
    small = pygame.transform.smoothscale(surf, (W // 6, H // 6))
    thr = pygame.Surface(small.get_size())
    thr.fill((150, 150, 150))
    small.blit(thr, (0, 0), special_flags=pygame.BLEND_SUB)
    if hasattr(pygame.transform, "box_blur"):
        small = pygame.transform.box_blur(small, 3)
    k = pygame.Surface(small.get_size())
    k.fill((int(255 * strength),) * 3)
    small.blit(k, (0, 0), special_flags=pygame.BLEND_MULT)
    big = pygame.transform.smoothscale(small, (W, H))
    surf.blit(big, (0, 0), special_flags=pygame.BLEND_ADD)
