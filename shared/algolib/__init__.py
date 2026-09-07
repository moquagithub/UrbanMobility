"""
Primitives numériques remplaçant les bibliothèques non installables.

RAISON D'ÊTRE — La politique d'imports du projet n'autorise que numpy, pandas,
scipy, sklearn, statsmodels et matplotlib : les bibliothèques à extensions C
fragiles (rtree, pyproj, shapely, networkx, numba, filterpy, pykalman, GPy,
tslearn, pywt) provoquent des crashs natifs qui tuent le kernel Jupyter entier,
pas seulement l'algorithme fautif.

Conséquence observée le 2026-07-28 : la moitié du backlog qualité de traces_gps
(9 issues sur 18) consistait à demander au LLM de réimplémenter un processus
gaussien, un DTW, un filtre de Kalman, une transformée en ondelettes ou des
algorithmes de graphes **en numpy pur, de mémoire, à chaque tentative**. Les
modèles gratuits disponibles échouaient systématiquement, et rien n'était
capitalisé d'un cycle sur l'autre : chaque réparation repartait de zéro.

Ce module inverse le problème. Les primitives sont écrites et testées UNE fois,
ici ; la réparation LLM se réduit alors à remplacer un import par un autre —
une transformation que même un petit modèle réussit. C'est la différence entre
demander « réinvente scipy » et « appelle cette fonction ».

Contrat : uniquement numpy / scipy / sklearn, aucune extension C fragile, aucune
dépendance à ajouter. Importable depuis les notebooks générés, qui s'exécutent
avec la racine du projet comme répertoire courant.
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "haversine_km", "nearest_neighbors",
    "dtw_distance", "dtw_path",
    "kalman_smooth",
    "cwt",
    "mst_edges", "shortest_paths", "connected_components",
    "gp_fit_predict",
    "change_points",
]


# ── Géospatial : remplace geopy / shapely / pyproj / rtree ───────────────────

def haversine_km(lat1, lon1, lat2, lon2):
    """
    Distance orthodromique en kilomètres, vectorisée numpy.

    Remplace geopy.distance et les calculs shapely/pyproj sur coordonnées
    géographiques. Accepte scalaires ou tableaux (diffusion numpy usuelle).
    """
    lat1, lon1, lat2, lon2 = map(np.asarray, (lat1, lon1, lat2, lon2))
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2.0) ** 2
    return 6371.0088 * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def nearest_neighbors(lat, lon, k=2):
    """
    k plus proches voisins entre points géographiques.

    Remplace l'indexation spatiale de rtree / shapely.STRtree. Retourne
    (distances_km, indices), voisins triés par distance, le point lui-même inclus
    en position 0 — comme le fait scipy.spatial.cKDTree.

    L'index est construit sur des coordonnées cartésiennes 3D unitaires : cela
    évite la discontinuité de l'antiméridien et des pôles qu'un KDTree posé
    directement sur (lat, lon) introduirait. La distance cordale renvoyée par le
    KDTree est ensuite reconvertie en distance de surface.
    """
    from scipy.spatial import cKDTree

    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    p, l = np.radians(lat), np.radians(lon)
    xyz = np.column_stack([np.cos(p) * np.cos(l), np.cos(p) * np.sin(l), np.sin(p)])

    k = min(int(k), len(xyz))
    chord, idx = cKDTree(xyz).query(xyz, k=k)
    chord = np.atleast_2d(chord.T).T if k > 1 else chord.reshape(-1, 1)
    idx = np.atleast_2d(idx.T).T if k > 1 else idx.reshape(-1, 1)
    # corde -> arc : d = 2R·arcsin(corde/2)
    dist_km = 6371.0088 * 2.0 * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))
    return dist_km, idx


# ── DTW : remplace tslearn / fastdtw ─────────────────────────────────────────

def _dtw_matrix(a, b, radius=None):
    """Matrice de coûts cumulés DTW, remplie ligne à ligne en numpy."""
    a = np.asarray(a, dtype=float).ravel()
    b = np.asarray(b, dtype=float).ravel()
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        raise ValueError("dtw : séquence vide")

    inf = np.inf
    D = np.full((n + 1, m + 1), inf)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        if radius is None:
            lo, hi = 1, m
        else:
            # bande de Sakoe-Chiba : borne le calcul à O(n·radius) au lieu de
            # O(n·m). Sur 5000 points, c'est la différence entre une seconde et
            # plusieurs minutes — le timeout d'exécution était atteint sans elle.
            centre = int(round(i * m / n))
            lo = max(1, centre - radius)
            hi = min(m, centre + radius)
        if lo > hi:
            continue
        cost = np.abs(a[i - 1] - b[lo - 1:hi])
        prev = D[i - 1, lo - 1:hi]          # diagonale
        up = D[i - 1, lo:hi + 1]            # au-dessus
        best = np.minimum(prev, up)
        # la dépendance à gauche est séquentielle : une passe scalaire sur la bande
        row = D[i]
        for j_off, (c, bst) in enumerate(zip(cost, best)):
            j = lo + j_off
            row[j] = c + min(bst, row[j - 1])
    return D


def dtw_distance(a, b, radius=None):
    """
    Distance DTW entre deux séries. Remplace tslearn.metrics.dtw et fastdtw.

    radius borne la déformation temporelle (bande de Sakoe-Chiba) et rend le
    calcul praticable sur de longues séries ; None = DTW exact.
    """
    D = _dtw_matrix(a, b, radius)
    return float(D[-1, -1])


def dtw_path(a, b, radius=None):
    """
    Chemin d'alignement DTW, liste de paires (i, j) en indices 0-based.
    Remplace tslearn.metrics.dtw_path.
    """
    D = _dtw_matrix(a, b, radius)
    i, j = len(np.asarray(a).ravel()), len(np.asarray(b).ravel())
    path = []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        step = np.argmin([D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]])
        if step == 0:
            i, j = i - 1, j - 1
        elif step == 1:
            i -= 1
        else:
            j -= 1
    return path[::-1]


# ── Kalman : remplace filterpy / pykalman ────────────────────────────────────

def kalman_smooth(z, process_var=1e-3, measurement_var=1e-1, dt=1.0):
    """
    Lissage de Kalman à vitesse constante sur une série 1D bruitée.
    Remplace pykalman.KalmanFilter et filterpy pour l'usage courant du projet.

    Retourne (position_lissée, vitesse_estimée), même longueur que z. Les valeurs
    manquantes (NaN) sont traitées comme des absences de mesure : la prédiction
    se poursuit sans étape de correction, ce qui interpole naturellement les trous.
    """
    z = np.asarray(z, dtype=float).ravel()
    n = len(z)
    if n == 0:
        return np.array([]), np.array([])

    F = np.array([[1.0, dt], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = process_var * np.array([[dt ** 3 / 3.0, dt ** 2 / 2.0],
                                [dt ** 2 / 2.0, dt]])
    R = np.array([[float(measurement_var)]])

    first = z[~np.isnan(z)]
    x = np.array([[first[0] if first.size else 0.0], [0.0]])
    P = np.eye(2) * 1.0

    pos = np.empty(n)
    vel = np.empty(n)
    for t in range(n):
        x = F @ x
        P = F @ P @ F.T + Q
        if not np.isnan(z[t]):
            y = np.array([[z[t]]]) - H @ x
            S = H @ P @ H.T + R
            K = P @ H.T @ np.linalg.inv(S)
            x = x + K @ y
            P = (np.eye(2) - K @ H) @ P
        pos[t], vel[t] = x[0, 0], x[1, 0]
    return pos, vel


# ── Ondelettes : remplace pywt (et scipy.signal.cwt, retiré de SciPy) ────────

def cwt(x, scales):
    """
    Transformée en ondelettes continue, ondelette chapeau mexicain.
    Remplace pywt.cwt et scipy.signal.cwt (cette dernière a été RETIRÉE de SciPy).

    Retourne un tableau (len(scales), len(x)) : une ligne par échelle. Le chapeau
    mexicain étant la dérivée seconde d'une gaussienne, la convolution se fait
    directement via gaussian_filter1d(order=2), sans construire le noyau.
    """
    from scipy.ndimage import gaussian_filter1d

    x = np.asarray(x, dtype=float).ravel()
    scales = np.atleast_1d(np.asarray(scales, dtype=float))
    out = np.empty((len(scales), len(x)))
    for i, s in enumerate(scales):
        s = max(float(s), 1e-6)
        # normalisation en s^{3/2} : rend les coefficients comparables entre échelles
        out[i] = -(s ** 1.5) * gaussian_filter1d(x, sigma=s, order=2, mode="nearest")
    return out


# ── Graphes : remplace networkx / osmnx ──────────────────────────────────────

def _to_sparse(adjacency):
    from scipy.sparse import csr_matrix, issparse

    return adjacency if issparse(adjacency) else csr_matrix(np.asarray(adjacency, dtype=float))


def mst_edges(adjacency):
    """
    Arbre couvrant de poids minimal. Remplace networkx.minimum_spanning_tree.
    Retourne une liste de (i, j, poids), arêtes non orientées.
    """
    from scipy.sparse.csgraph import minimum_spanning_tree

    t = minimum_spanning_tree(_to_sparse(adjacency)).tocoo()
    return [(int(i), int(j), float(w)) for i, j, w in zip(t.row, t.col, t.data)]


def shortest_paths(adjacency, directed=False):
    """
    Matrice des plus courts chemins entre tous les couples.
    Remplace networkx.all_pairs_shortest_path_length / dijkstra.
    """
    from scipy.sparse.csgraph import shortest_path

    return shortest_path(_to_sparse(adjacency), directed=directed)


def connected_components(adjacency, directed=False):
    """
    Composantes connexes. Remplace networkx.connected_components.
    Retourne (n_composantes, étiquette_par_noeud).
    """
    from scipy.sparse.csgraph import connected_components as _cc

    n, labels = _cc(_to_sparse(adjacency), directed=directed)
    return int(n), labels


# ── Processus gaussien : remplace GPy ────────────────────────────────────────

def gp_fit_predict(X, y, X_new=None, length_scale=1.0, noise=1e-2):
    """
    Régression par processus gaussien. Remplace GPy.models.GPRegression.

    Retourne (moyenne, écart-type) sur X_new (ou sur X si X_new est None). Noyau
    RBF + terme de bruit, hyperparamètres optimisés par sklearn.
    """
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, WhiteKernel

    X = np.asarray(X, dtype=float)
    X = X.reshape(-1, 1) if X.ndim == 1 else X
    y = np.asarray(y, dtype=float).ravel()
    Xq = X if X_new is None else np.asarray(X_new, dtype=float)
    Xq = Xq.reshape(-1, 1) if Xq.ndim == 1 else Xq

    kernel = RBF(length_scale=length_scale) + WhiteKernel(noise_level=noise)
    gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True)
    gp.fit(X, y)
    mean, std = gp.predict(Xq, return_std=True)
    return mean, std


# ── Ruptures : remplace ruptures ─────────────────────────────────────────────

def change_points(x, window=25, threshold=3.0):
    """
    Détection de ruptures par différence de moyennes glissantes.
    Remplace ruptures.Pelt/Binseg pour l'usage du projet.

    Retourne les indices où la statistique de rupture dépasse `threshold` écarts-
    types robustes (MAD), en ne gardant qu'un point par plateau contigu — sinon
    une même rupture ressort sur toute la largeur de la fenêtre.
    """
    x = np.asarray(x, dtype=float).ravel()
    n = len(x)
    w = max(2, int(window))
    if n < 2 * w:
        return np.array([], dtype=int)

    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(x))])
    left = (c[w:n - w + 1] - c[0:n - 2 * w + 1]) / w
    right = (c[2 * w:n + 1] - c[w:n - w + 1]) / w
    stat = np.abs(right - left)

    med = np.median(stat)
    mad = np.median(np.abs(stat - med))
    scale = mad * 1.4826 if mad > 0 else (stat.std() or 1.0)
    flag = stat > med + threshold * scale

    hits = np.flatnonzero(flag)
    if hits.size == 0:
        return np.array([], dtype=int)

    # Une rupture franchit le seuil sur toute la largeur de la fenêtre. Retenir le
    # premier point de chaque plateau la datait systématiquement trop tôt (mesuré :
    # 127 au lieu de 150) ; on garde le maximum de la statistique, qui tombe sur la
    # rupture réelle.
    groups = np.split(hits, np.flatnonzero(np.diff(hits) > w) + 1)
    return np.array([g[int(np.argmax(stat[g]))] + w for g in groups], dtype=int)
