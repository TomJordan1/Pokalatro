// 6-Player Linear External-Sampling MCCFR Blueprint Trainer (Pluribus-style)
// Entrena 6 sillas simultaneas (UTG, HJ, CO, BTN, SB, BB) jugando millones de
// manos reales de 52 cartas en Texas Hold'em No-Limit con pesos lineales t
// (Linear MCCFR, Brown & Sandholm 2019) y exporta tablas para Python puro.

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

static int STRAIGHT[8192];
static int TOP5[8192];

static void init_eval() {
    for (int m = 0; m < 8192; ++m) {
        int st = -1;
        for (int high = 12; high >= 4; --high) {
            int win = 0x1F << (high - 4);
            if ((m & win) == win) { st = high; break; }
        }
        if (st < 0 && (m & 0x100F) == 0x100F) st = 3;
        STRAIGHT[m] = st;
        int val = 0, n = 0;
        for (int r = 12; r >= 0; --r) {
            if ((m >> r) & 1) {
                val = val * 16 + r;
                if (++n == 5) break;
            }
        }
        TOP5[m] = val;
    }
}

static inline int top_n(int mask, int n) {
    int val = 0;
    for (int r = 12; r >= 0 && n > 0; --r) {
        if ((mask >> r) & 1) { val = val * 16 + r; --n; }
    }
    while (n-- > 0) val *= 16;
    return val;
}

static int eval_cards(const int* cards, int n) {
    int cnt[13] = {0}, sc[4] = {0}, sm[4] = {0}, mask = 0;
    for (int i = 0; i < n; ++i) {
        int c = cards[i], r = c >> 2, s = c & 3;
        cnt[r]++; sc[s]++; sm[s] |= (1 << r); mask |= (1 << r);
    }
    for (int s = 0; s < 4; ++s) {
        if (sc[s] >= 5) {
            int m = sm[s], st = STRAIGHT[m];
            return st >= 0 ? ((8 << 20) | st) : ((5 << 20) | TOP5[m]);
        }
    }
    int four = -1, t0 = -1, t1 = -1, p0 = -1, p1 = -1;
    for (int r = 12; r >= 0; --r) {
        int k = cnt[r];
        if (k == 4) four = r;
        else if (k == 3) { if (t0 < 0) t0 = r; else if (t1 < 0) t1 = r; }
        else if (k == 2) { if (p0 < 0) p0 = r; else if (p1 < 0) p1 = r; }
    }
    if (four >= 0) return (7 << 20) | (four << 4) | top_n(mask & ~(1 << four), 1);
    if (t0 >= 0 && (t1 >= 0 || p0 >= 0)) {
        int p = (t1 >= 0 && (p0 < 0 || t1 > p0)) ? t1 : p0;
        return (6 << 20) | (t0 << 4) | p;
    }
    int st = STRAIGHT[mask];
    if (st >= 0) return (4 << 20) | st;
    if (t0 >= 0) return (3 << 20) | (t0 << 8) | top_n(mask & ~(1 << t0), 2);
    if (p1 >= 0) return (2 << 20) | (p0 << 8) | (p1 << 4) | top_n(mask & ~(1 << p0) & ~(1 << p1), 1);
    if (p0 >= 0) return (1 << 20) | (p0 << 12) | top_n(mask & ~(1 << p0), 3);
    return TOP5[mask];
}

// Orden canonico exacto de HAND_ORDER en app/blueprint_data.py (0=AA .. 168=72o)
struct HandKey { int hi, lo; bool suited; };
static int HAND_IDX[13][13][2];

static void load_hand_order(const char* bp_path) {
    // Parsear HAND_ORDER directamente de app/blueprint_data.py para 100% consistencia
    std::ifstream in(bp_path);
    std::string line;
    int idx = 0;
    bool inside = false;
    while (std::getline(in, line)) {
        if (line.find("HAND_ORDER = (") != std::string::npos) { inside = true; continue; }
        if (!inside) continue;
        if (line.find(")") == 0 || line == ")") break;
        size_t pos = 0;
        while ((pos = line.find('(', pos)) != std::string::npos) {
            int r1 = 0, r2 = 0;
            char bbuf[16] = {0};
            if (sscanf(line.c_str() + pos, "(%d, %d, %15[^)])", &r1, &r2, bbuf) == 3) {
                int s = (bbuf[0] == 'T') ? 1 : 0;
                HAND_IDX[r1 - 2][r2 - 2][s] = idx++;
            }
            pos++;
        }
    }
    if (idx != 169) {
        fprintf(stderr, "Error cargando HAND_ORDER: leidos %d\n", idx);
        exit(1);
    }
}

static inline int hand_class_idx(int c0, int c1) {
    int r0 = c0 >> 2, r1 = c1 >> 2;
    if (r0 < r1) std::swap(r0, r1);
    int s = ((c0 & 3) == (c1 & 3)) ? 1 : 0;
    return HAND_IDX[r0][r1][s];
}

// Clasificador rapido postflop identico a app/abstraccion.py (made_tier 0..6, draw_tier 0..3)
static inline void classify_postflop(int c0, int c1, const int* board, int nb, int& made_tier, int& draw_tier) {
    int all[7];
    all[0] = c0; all[1] = c1;
    int bcnt[13] = {0}, bsuit[4] = {0}, bmask = 0, max_b = -1;
    for (int i = 0; i < nb; ++i) {
        all[2 + i] = board[i];
        int r = board[i] >> 2, s = board[i] & 3;
        bcnt[r]++; bsuit[s]++; bmask |= (1 << r);
        if (r > max_b) max_b = r;
    }
    int ev_tot = eval_cards(all, 2 + nb);
    int cat_tot = ev_tot >> 20;
    int r0 = c0 >> 2, r1 = c1 >> 2, s0 = c0 & 3, s1 = c1 & 3;
    int hi_r = std::max(r0, r1), lo_r = std::min(r0, r1);

    // Categoria propia (descartar pares solo en mesa)
    int cat_prop = cat_tot;
    if (nb >= 3 && cat_tot == 1) {
        if (r0 != r1 && bcnt[r0] == 0 && bcnt[r1] == 0) cat_prop = 0;
    } else if (nb >= 3 && cat_tot == 2) {
        int pares_b = 0;
        for (int r = 0; r < 13; ++r) if (bcnt[r] >= 2) pares_b++;
        if (pares_b >= 2 && r0 != r1 && bcnt[r0] == 0 && bcnt[r1] == 0) cat_prop = 0;
        else if (pares_b == 1 && (r0 != r1 && bcnt[r0] == 0 && bcnt[r1] == 0)) cat_prop = 0;
        else if (pares_b == 1 && !((r0 != r1 && bcnt[r0] >= 1 && bcnt[r1] >= 1))) cat_prop = 1;
    }

    bool solo_mesa = false;
    if (nb == 5 && cat_tot >= 4 && eval_cards(board, 5) == ev_tot) solo_mesa = true;

    if (cat_prop >= 4 && !solo_mesa) made_tier = 6;
    else if (cat_prop == 3) made_tier = 5;
    else if (cat_prop == 2) made_tier = 4;
    else if (cat_prop == 1) {
        if (r0 == r1 && r0 > max_b) made_tier = 3;
        else if ((r0 == max_b && bcnt[r0] == 1) || (r1 == max_b && bcnt[r1] == 1)) {
            int k = (r0 == max_b) ? r1 : r0;
            made_tier = (k >= 9) ? 3 : 2;
        } else if (r0 == r1) {
            int seg = -1;
            for (int r = max_b - 1; r >= 0; --r) if (bcnt[r] > 0) { seg = r; break; }
            made_tier = (r0 > seg) ? 2 : 1;
        } else if (bcnt[r0] == 1 || bcnt[r1] == 1) made_tier = 1;
        else made_tier = 0;
    } else made_tier = 0;

    draw_tier = 0;
    if (nb < 5 && made_tier < 5) {
        bool fd = false, two_fd = false;
        int ss[2] = {s0, s1};
        for (int idx = 0; idx < 2; ++idx) {
            int s = ss[idx];
            int cp = (s0 == s ? 1 : 0) + (s1 == s ? 1 : 0);
            if (bsuit[s] + cp == 4) {
                if (cp == 2) { fd = true; two_fd = true; break; }
                int cr = (s0 == s) ? r0 : r1;
                if (cr >= 10) { fd = true; break; }
            }
        }
        int s_outs = 0;
        int mall = bmask | (1 << r0) | (1 << r1);
        if (STRAIGHT[mall] < 0) {
            bool has_p = ((bmask >> r0) & 1) == 0 || ((bmask >> r1) & 1) == 0;
            if (has_p) {
                for (int cand = 0; cand < 13; ++cand) {
                    if ((mall >> cand) & 1) continue;
                    int st_a = STRAIGHT[mall | (1 << cand)];
                    if (st_a >= 0 && st_a > STRAIGHT[bmask | (1 << cand)]) s_outs++;
                }
            }
        }
        bool ovc = (hi_r > max_b && lo_r > max_b && hi_r >= 10);
        if (fd && (s_outs >= 1 || ovc || (two_fd && made_tier >= 2))) draw_tier = 3;
        else if (fd || s_outs >= 2) draw_tier = 2;
        else if (s_outs == 1 || (ovc && made_tier == 0)) draw_tier = 1;
    }
}

static inline uint64_t splitmix64(uint64_t& x) {
    uint64_t z = (x += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

static inline double urand(uint64_t& rng) {
    return (splitmix64(rng) >> 11) * (1.0 / 9007199254740992.0);
}

// ============================================================================
// InfoSets para 6-max Linear External-Sampling MCCFR
// ============================================================================
// Preflop: 6 posiciones (0=UTG..5=BB) x 4 spots (0=unopened, 1=facing_limp, 2=facing_open, 3=facing_3bet) x 169 manos -> 3 acciones (fold/check, call, raise)
static constexpr int N_PRE_IS = 6 * 4 * 169;
// Postflop: 3 streets (0=flop,1=turn,2=river) x 3 spr_b x 2 pos_b x 2 mw_b x 7 made x 4 draw x 5 facing -> 3 acciones
static constexpr int N_POST_IS = 3 * 3 * 2 * 2 * 7 * 4 * 5;

struct InfoSet3 {
    double reg[3] = {0.0, 0.0, 0.0};
    double sum[3] = {0.0, 0.0, 0.0};
};

static inline int pre_key(int pos, int spot, int h) {
    return ((pos * 4 + spot) * 169) + h;
}

static inline int post_key(int st, int spr_b, int pos_b, int mw_b, int made, int draw, int fac) {
    return ((((((st * 3 + spr_b) * 2 + pos_b) * 2 + mw_b) * 7 + made) * 4 + draw) * 5) + fac;
}

static inline void regret_matching(const double reg[3], const bool valid[3], double strat[3]) {
    double s = 0.0;
    for (int a = 0; a < 3; ++a) {
        strat[a] = (valid[a] && reg[a] > 0.0) ? reg[a] : 0.0;
        s += strat[a];
    }
    if (s > 1e-12) {
        double inv = 1.0 / s;
        for (int a = 0; a < 3; ++a) strat[a] *= inv;
    } else {
        int nv = 0;
        for (int a = 0; a < 3; ++a) if (valid[a]) nv++;
        double p = nv > 0 ? 1.0 / nv : 0.0;
        for (int a = 0; a < 3; ++a) strat[a] = valid[a] ? p : 0.0;
    }
}

struct Deal6 {
    int hole[6][2];
    int board[5];
    int hidx[6];
    int made[6][3];
    int draw[6][3];
    int hv[6];
};

static void deal_hand(Deal6& d, uint64_t& rng) {
    int deck[52];
    for (int i = 0; i < 52; ++i) deck[i] = i;
    for (int i = 0; i < 17; ++i) {
        int j = i + (int)(splitmix64(rng) % (52 - i));
        std::swap(deck[i], deck[j]);
    }
    for (int p = 0; p < 6; ++p) {
        d.hole[p][0] = deck[2 * p];
        d.hole[p][1] = deck[2 * p + 1];
        d.hidx[p] = hand_class_idx(d.hole[p][0], d.hole[p][1]);
    }
    for (int i = 0; i < 5; ++i) d.board[i] = deck[12 + i];
    for (int p = 0; p < 6; ++p) {
        classify_postflop(d.hole[p][0], d.hole[p][1], d.board, 3, d.made[p][0], d.draw[p][0]);
        classify_postflop(d.hole[p][0], d.hole[p][1], d.board, 4, d.made[p][1], d.draw[p][1]);
        classify_postflop(d.hole[p][0], d.hole[p][1], d.board, 5, d.made[p][2], d.draw[p][2]);
        int c7[7] = {d.hole[p][0], d.hole[p][1], d.board[0], d.board[1], d.board[2], d.board[3], d.board[4]};
        d.hv[p] = eval_cards(c7, 7);
    }
}

// Estado de una mano 6-max en ciegas (stack inicial = 100 BB, SB=0.5, BB=1.0)
struct GameState {
    int street;          // 0=pre, 1=flop, 2=turn, 3=river
    double pot;
    double cur_bet;
    double contrib[6];
    double street_bet[6];
    bool active[6];      // sigue en la mano
    bool acted[6];       // ya actuo desde la ultima subida en esta calle
    int raises_street;
    int limps_pre;
    int turn_idx;        // indice en orden de calle (0..5)
};

// Orden de accion: preflop UTG(0)->HJ(1)->CO(2)->BTN(3)->SB(4)->BB(5); postflop SB(4)->BB(5)->UTG(0)->HJ(1)->CO(2)->BTN(3)
static constexpr int ORDER_PRE[6]  = {0, 1, 2, 3, 4, 5};
static constexpr int ORDER_POST[6] = {4, 5, 0, 1, 2, 3};

struct WorkerData {
    std::vector<InfoSet3> pre;
    std::vector<InfoSet3> post;
    uint64_t rng;
    WorkerData(uint64_t seed) : pre(N_PRE_IS), post(N_POST_IS), rng(seed) {}
};

static double payoff_showdown(const Deal6& d, const GameState& gs, int traverser) {
    if (!gs.active[traverser]) return -gs.contrib[traverser];
    int best = -1, nwin = 0;
    for (int p = 0; p < 6; ++p) {
        if (!gs.active[p]) continue;
        if (d.hv[p] > best) { best = d.hv[p]; nwin = 1; }
        else if (d.hv[p] == best) nwin++;
    }
    if (d.hv[traverser] == best) return (gs.pot / nwin) - gs.contrib[traverser];
    return -gs.contrib[traverser];
}

static int count_active(const GameState& gs) {
    int c = 0;
    for (int p = 0; p < 6; ++p) if (gs.active[p]) c++;
    return c;
}

static int find_next_actor(const GameState& gs, int start_idx) {
    const int* ord = (gs.street == 0) ? ORDER_PRE : ORDER_POST;
    for (int k = 0; k < 6; ++k) {
        int idx = (start_idx + k) % 6;
        int p = ord[idx];
        if (!gs.active[p]) continue;
        if (gs.contrib[p] >= 99.99) continue; // all-in
        if (!gs.acted[p] || gs.street_bet[p] < gs.cur_bet - 1e-6) return idx;
    }
    return -1;
}

static int count_can_act(const GameState& gs) {
    int c = 0;
    for (int p = 0; p < 6; ++p) if (gs.active[p] && gs.contrib[p] < 99.99) c++;
    return c;
}

static double traverse(
    WorkerData& wd, const Deal6& d, const GameState& gs,
    int traverser, double weight_t
) {
    int n_act = count_active(gs);
    if (n_act <= 1) {
        if (gs.active[traverser]) return gs.pot - gs.contrib[traverser];
        return -gs.contrib[traverser];
    }

    int next_idx = find_next_actor(gs, gs.turn_idx);
    if (next_idx < 0 || count_can_act(gs) <= 1) {
        if (gs.street >= 3 || count_can_act(gs) <= 1) {
            return payoff_showdown(d, gs, traverser);
        }
        GameState nxt = gs;
        nxt.street++;
        nxt.cur_bet = 0.0;
        nxt.raises_street = 0;
        nxt.turn_idx = 0;
        for (int p = 0; p < 6; ++p) { nxt.street_bet[p] = 0.0; nxt.acted[p] = false; }
        return traverse(wd, d, nxt, traverser, weight_t);
    }

    const int* ord = (gs.street == 0) ? ORDER_PRE : ORDER_POST;
    int p = ord[next_idx];
    double to_call = std::max(0.0, gs.cur_bet - gs.street_bet[p]);
    double rem = std::max(0.0, 100.0 - gs.contrib[p]);
    bool can_raise = (rem > to_call + 0.5) && (gs.raises_street < (gs.street == 0 ? 2 : 2));

    bool valid[3] = {true, true, can_raise};
    int is_id = 0;
    InfoSet3* iset = nullptr;

    if (gs.street == 0) {
        int spot = 0;
        if (gs.raises_street == 0) spot = (gs.limps_pre > 0) ? 1 : 0;
        else if (gs.raises_street == 1) spot = 2;
        else spot = 3;
        // En preflop: a0=fold (o check si to_call==0), a1=call (solo si to_call>0), a2=raise
        if (to_call <= 1e-6) valid[1] = false; // BB check es a0, raise es a2
        is_id = pre_key(p, spot, d.hidx[p]);
        iset = &wd.pre[is_id];
    } else {
        int st_idx = gs.street - 1;
        double spr = rem / std::max(1.0, gs.pot);
        int spr_b = (spr < 2.2) ? 0 : (spr < 6.0 ? 1 : 2);
        // En posicion si nadie activo habla despues en ORDER_POST
        bool in_pos = true;
        for (int k = next_idx + 1; k < 6; ++k) {
            if (gs.active[ORDER_POST[k]]) { in_pos = false; break; }
        }
        int pos_b = in_pos ? 1 : 0;
        int mw_b = (n_act > 2) ? 1 : 0;
        int made = d.made[p][st_idx];
        int draw = (st_idx == 2) ? 0 : d.draw[p][st_idx];
        int fac = 0;
        if (to_call <= 1e-6) {
            fac = (next_idx > 0 && gs.raises_street == 0) ? 1 : 0;
            // a0=check, a1=bet 0.5p, a2=bet 1.0p
            valid[1] = can_raise;
            valid[2] = can_raise;
        } else {
            double frac = to_call / std::max(1.0, gs.pot);
            if (gs.raises_street >= 2) fac = 4;
            else fac = (frac <= 0.60) ? 2 : 3;
            // a0=fold, a1=call, a2=raise 0.85p
            valid[0] = true;
            valid[1] = true;
            valid[2] = can_raise;
        }
        is_id = post_key(st_idx, spr_b, pos_b, mw_b, made, draw, fac);
        iset = &wd.post[is_id];
    }

    double strat[3];
    regret_matching(iset->reg, valid, strat);

    auto apply_action = [&](int a) -> GameState {
        GameState nxt = gs;
        nxt.turn_idx = (next_idx + 1) % 6;
        nxt.acted[p] = true;
        if (gs.street == 0) {
            if (a == 0) {
                if (to_call > 1e-6) nxt.active[p] = false;
            } else if (a == 1) {
                double put = std::min(rem, to_call);
                nxt.contrib[p] += put; nxt.street_bet[p] += put; nxt.pot += put;
                if (gs.raises_street == 0) nxt.limps_pre++;
            } else {
                double target = (gs.raises_street == 0) ? (3.0 + gs.limps_pre) : (gs.cur_bet * 3.0);
                double need = std::min(rem, std::max(to_call + 1.0, target - gs.street_bet[p]));
                nxt.contrib[p] += need; nxt.street_bet[p] += need; nxt.pot += need;
                if (nxt.street_bet[p] > nxt.cur_bet) {
                    nxt.cur_bet = nxt.street_bet[p];
                    nxt.raises_street++;
                    for (int q = 0; q < 6; ++q) if (q != p) nxt.acted[q] = false;
                }
            }
        } else {
            if (to_call <= 1e-6) {
                if (a == 0) {
                    // check
                } else {
                    double mult = (a == 1) ? 0.50 : 1.00;
                    double bet = std::min(rem, std::max(1.0, gs.pot * mult));
                    nxt.contrib[p] += bet; nxt.street_bet[p] += bet; nxt.pot += bet;
                    nxt.cur_bet = nxt.street_bet[p];
                    nxt.raises_street++;
                    for (int q = 0; q < 6; ++q) if (q != p) nxt.acted[q] = false;
                }
            } else {
                if (a == 0) {
                    nxt.active[p] = false;
                } else if (a == 1) {
                    double put = std::min(rem, to_call);
                    nxt.contrib[p] += put; nxt.street_bet[p] += put; nxt.pot += put;
                } else {
                    double extra = std::max(to_call, (gs.pot + to_call) * 0.85);
                    double put = std::min(rem, to_call + extra);
                    nxt.contrib[p] += put; nxt.street_bet[p] += put; nxt.pot += put;
                    if (nxt.street_bet[p] > nxt.cur_bet) {
                        nxt.cur_bet = nxt.street_bet[p];
                        nxt.raises_street++;
                        for (int q = 0; q < 6; ++q) if (q != p) nxt.acted[q] = false;
                    }
                }
            }
        }
        return nxt;
    };

    if (p == traverser) {
        double vals[3] = {0.0, 0.0, 0.0};
        double ev = 0.0;
        for (int a = 0; a < 3; ++a) {
            if (!valid[a]) continue;
            GameState nxt = apply_action(a);
            vals[a] = traverse(wd, d, nxt, traverser, weight_t);
            ev += strat[a] * vals[a];
        }
        // Linear MCCFR regret update (con piso suave para convergencia rapida)
        for (int a = 0; a < 3; ++a) {
            if (!valid[a]) continue;
            double nr = iset->reg[a] + weight_t * (vals[a] - ev);
            iset->reg[a] = std::max(-1e7, nr);
        }
        return ev;
    } else {
        // Muestreo externo de los otros 5 jugadores + actualizacion lineal de estrategia promedio
        double u = urand(wd.rng), acc = 0.0;
        int chosen = 0;
        for (int a = 0; a < 3; ++a) {
            if (!valid[a]) continue;
            acc += strat[a];
            chosen = a;
            if (u <= acc) break;
        }
        for (int a = 0; a < 3; ++a) {
            if (valid[a]) iset->sum[a] += weight_t * strat[a];
        }
        GameState nxt = apply_action(chosen);
        return traverse(wd, d, nxt, traverser, weight_t);
    }
}

static void worker_run(WorkerData* wd, int iters, int tid) {
    for (int t = 1; t <= iters; ++t) {
        Deal6 d;
        deal_hand(d, wd->rng);
        double w_t = (double)t; // Linear MCCFR: iteracion t pesa t
        // Actualizar las 6 sillas simultaneamente sobre el mismo reparto
        for (int p = 0; p < 6; ++p) {
            GameState gs{};
            gs.street = 0;
            gs.pot = 1.5;
            gs.cur_bet = 1.0;
            for (int i = 0; i < 6; ++i) {
                gs.active[i] = true;
                gs.acted[i] = false;
                gs.contrib[i] = 0.0;
                gs.street_bet[i] = 0.0;
            }
            gs.contrib[4] = 0.5; gs.street_bet[4] = 0.5; // SB
            gs.contrib[5] = 1.0; gs.street_bet[5] = 1.0; // BB
            gs.raises_street = 0;
            gs.limps_pre = 0;
            gs.turn_idx = 0;
            traverse(*wd, d, gs, p, w_t);
        }
        if (tid == 0 && t % 50000 == 0) {
            fprintf(stderr, "Thread 0: %d / %d manos 6-max (x10 hilos = %dk manos)\n", t, iters, (t * 10) / 1000);
        }
    }
}

int main(int argc, char** argv) {
    const char* bp_path = (argc > 1) ? argv[1] : "app/blueprint_data.py";
    int n_threads = (argc > 2) ? atoi(argv[2]) : 10;
    int iters_per_thread = (argc > 3) ? atoi(argv[3]) : 200000;

    init_eval();
    load_hand_order(bp_path);

    fprintf(stderr, "Entrenando 6-max Linear External-Sampling MCCFR: %d hilos x %d manos = %d manos reales...\n",
            n_threads, iters_per_thread, n_threads * iters_per_thread);

    std::vector<WorkerData*> workers;
    std::vector<std::thread> threads;
    for (int t = 0; t < n_threads; ++t) {
        workers.push_back(new WorkerData(20261121ULL + (uint64_t)t * 1000003ULL));
        threads.emplace_back(worker_run, workers[t], iters_per_thread, t);
    }
    for (auto& th : threads) th.join();

    // Agregar estrategias promedio de los N hilos
    std::vector<InfoSet3> agg_pre(N_PRE_IS), agg_post(N_POST_IS);
    for (auto* w : workers) {
        for (int i = 0; i < N_PRE_IS; ++i)
            for (int a = 0; a < 3; ++a) agg_pre[i].sum[a] += w->pre[i].sum[a];
        for (int i = 0; i < N_POST_IS; ++i)
            for (int a = 0; a < 3; ++a) agg_post[i].sum[a] += w->post[i].sum[a];
        delete w;
    }

    // Imprimir resumen de frecuencias preflop 6-max aprendidas (unopened raise%)
    const char* pnames[6] = {"UTG", "HJ", "CO", "BTN", "SB", "BB"};
    for (int p = 0; p < 5; ++p) {
        double r_sum = 0.0, c_sum = 0.0;
        for (int h = 0; h < 169; ++h) {
            auto& is = agg_pre[pre_key(p, 0, h)];
            double tot = is.sum[0] + is.sum[1] + is.sum[2];
            if (tot > 0) { r_sum += is.sum[2] / tot; c_sum += is.sum[1] / tot; }
        }
        fprintf(stderr, "6-max Linear MCCFR %s unopened -> open: %.1f%%, limp: %.1f%%\n",
                pnames[p], 100.0 * r_sum / 169.0, 100.0 * c_sum / 169.0);
    }

    // Exportar modulo Python puro app/blueprint_6max.py
    const char* out_path = (argc > 4) ? argv[4] : "app/blueprint_6max.py";
    FILE* f = fopen(out_path, "w");
    fprintf(f, "\"\"\"Blueprint 6-max entrenado con Linear External-Sampling MCCFR (6 sillas simultaneas).\n\n");
    fprintf(f, "GENERADO por tools/trainer_pluribus_6max.cpp (%d manos 6-max reales).\n", n_threads * iters_per_thread);
    fprintf(f, "Python puro sin dependencias externas.\n\"\"\"\n\n");

    fprintf(f, "# Clave: (pos_6max 0..5 [UTG,HJ,CO,BTN,SB,BB], spot 0..3 [unopen,vs_limp,vs_open,vs_3b])\n");
    fprintf(f, "# Valor: tupla de 169 tuplas (p_fold_o_check, p_call, p_raise)\n");
    fprintf(f, "PREFLOP_6MAX = {\n");
    for (int p = 0; p < 6; ++p) {
        for (int sp = 0; sp < 4; ++sp) {
            fprintf(f, "    (%d, %d): (\n", p, sp);
            for (int h = 0; h < 169; ++h) {
                auto& is = agg_pre[pre_key(p, sp, h)];
                double tot = is.sum[0] + is.sum[1] + is.sum[2];
                double p0 = 1.0, p1 = 0.0, p2 = 0.0;
                if (tot > 1e-9) { p0 = is.sum[0] / tot; p1 = is.sum[1] / tot; p2 = is.sum[2] / tot; }
                fprintf(f, "        (%.3f, %.3f, %.3f),\n", p0, p1, p2);
            }
            fprintf(f, "    ),\n");
        }
    }
    fprintf(f, "}\n\n");

    fprintf(f, "# Clave: (st 0..2, spr_b 0..2, pos_b 0..1, mw_b 0..1, made 0..6, draw 0..3, fac 0..4)\n");
    fprintf(f, "# Valor: (p_check_o_fold, p_small_o_call, p_big_o_raise)\n");
    fprintf(f, "POSTFLOP_6MAX = {\n");
    int written = 0;
    for (int st = 0; st < 3; ++st)
    for (int spr = 0; spr < 3; ++spr)
    for (int pos = 0; pos < 2; ++pos)
    for (int mw = 0; mw < 2; ++mw)
    for (int md = 0; md < 7; ++md)
    for (int dr = 0; dr < 4; ++dr)
    for (int fc = 0; fc < 5; ++fc) {
        auto& is = agg_post[post_key(st, spr, pos, mw, md, dr, fc)];
        double tot = is.sum[0] + is.sum[1] + is.sum[2];
        if (tot <= 1e-6) continue;
        fprintf(f, "    (%d,%d,%d,%d,%d,%d,%d):(%.3f,%.3f,%.3f),\n",
                st, spr, pos, mw, md, dr, fc, is.sum[0]/tot, is.sum[1]/tot, is.sum[2]/tot);
        written++;
    }
    fprintf(f, "}\n");
    fclose(f);
    fprintf(stderr, "Exportado %s (%d estados postflop 6-max cubiertos)\n", out_path, written);
    return 0;
}
