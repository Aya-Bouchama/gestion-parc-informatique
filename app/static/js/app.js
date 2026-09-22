/* Frontend Vue 3 — consomme l'API REST /api/v1 */
const { createApp } = Vue;
const API = "/api/v1";

async function api(path, options = {}) {
    const res = await fetch(API + path, options);
    if (res.status === 204) return null;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(body.message || "Erreur " + res.status);
        err.details = body.details || {};
        throw err;
    }
    return body;
}
const jsonOpts = (method, data) => ({
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
});

createApp({
    data() {
        return {
            tab: "inventaire",
            health: { ocr: false },
            materiels: [], chargement: false, q: "", timer: null,
            form: {}, erreurs: {}, envoi: false,
            ocr: { enCours: false, candidats: [], existant: null },
            panne: {},
            imp: { fichier: null, rapport: null, enCours: false },
            modele: { entraine: false }, predictions: [], entrainement: false,
            toasts: [],
            aujourdhui: new Date().toISOString().slice(0, 10),
            champs: [
                { name: "type", label: "Type" },
                { name: "numero_serie", label: "Numéro de série" },
                { name: "modele", label: "Modèle" },
                { name: "emplacement", label: "Emplacement" },
                { name: "id_gresa", label: "ID GRESA" },
                { name: "date_distribution", label: "Date de distribution", type: "date", max: new Date().toISOString().slice(0, 10) },
                { name: "nobre_materiel", label: "Nombre de matériel", type: "number", min: 1 },
            ],
        };
    },
    computed: {
        stats() {
            return {
                eleve: this.predictions.filter(p => p.risque === "élevé").length,
                moyen: this.predictions.filter(p => p.risque === "moyen").length,
                obsoletes: this.predictions.filter(p => p.obsolete).length,
            };
        },
    },
    methods: {
        notifier(msg, type = "success") {
            const id = Date.now() + Math.random();
            this.toasts.push({ id, msg, type });
            setTimeout(() => (this.toasts = this.toasts.filter(t => t.id !== id)), 4000);
        },
        dateFr(iso) { return iso ? iso.split("-").reverse().join("/") : ""; },
        badgeRisque(r) {
            return { "élevé": "text-bg-danger", "moyen": "text-bg-warning", "faible": "text-bg-success" }[r] || "text-bg-secondary";
        },

        // ---------- Inventaire ----------
        async charger() {
            this.chargement = true;
            try {
                const r = await api("/materiels" + (this.q ? "?q=" + encodeURIComponent(this.q) : ""));
                this.materiels = r.items;
            } catch (e) { this.notifier(e.message, "danger"); }
            this.chargement = false;
        },
        rechercherDebounce() { clearTimeout(this.timer); this.timer = setTimeout(this.charger, 300); },

        ouvrirForm(m = null) {
            this.erreurs = {};
            this.ocr = { enCours: false, candidats: [], existant: null };
            this.form = m ? { ...m } : { nobre_materiel: 1, date_distribution: this.aujourdhui };
            bootstrap.Modal.getOrCreateInstance("#modalForm").show();
        },
        async enregistrer() {
            this.envoi = true; this.erreurs = {};
            try {
                if (this.form.materiel_id) {
                    await api("/materiels/" + this.form.materiel_id, jsonOpts("PUT", this.form));
                    this.notifier("Matériel modifié");
                } else {
                    await api("/materiels", jsonOpts("POST", this.form));
                    this.notifier("Matériel ajouté");
                }
                bootstrap.Modal.getInstance("#modalForm").hide();
                this.charger();
            } catch (e) {
                this.erreurs = e.details;
                if (!Object.keys(e.details).length) this.notifier(e.message, "danger");
            }
            this.envoi = false;
        },
        async supprimer(m) {
            if (!confirm(`Supprimer ${m.type} ${m.numero_serie} ?`)) return;
            try {
                await api("/materiels/" + m.materiel_id, { method: "DELETE" });
                this.notifier("Matériel supprimé");
                this.charger();
            } catch (e) { this.notifier(e.message, "danger"); }
        },

        // ---------- OCR ----------
        async lancerOCR(ev) {
            const file = ev.target.files[0];
            if (!file) return;
            this.ocr = { enCours: true, candidats: [], existant: null };
            const fd = new FormData();
            fd.append("image", file);
            try {
                const r = await api("/ocr/numero-serie", { method: "POST", body: fd });
                this.ocr.candidats = r.candidats;
                this.ocr.existant = r.materiel_existant;
                if (r.numero_serie) {
                    this.form.numero_serie = r.numero_serie;
                    this.notifier("Numéro détecté : " + r.numero_serie);
                } else {
                    this.notifier("Aucun numéro de série détecté", "warning");
                }
            } catch (e) { this.notifier(e.message, "danger"); }
            this.ocr.enCours = false;
            ev.target.value = "";
        },

        // ---------- Import de fichier ----------
        ouvrirImport() {
            this.imp = { fichier: null, rapport: null, enCours: false };
            document.querySelector("#modalImport input[type=file]").value = "";
            bootstrap.Modal.getOrCreateInstance("#modalImport").show();
        },
        async importer(simulation) {
            this.imp.enCours = true;
            const fd = new FormData();
            fd.append("fichier", this.imp.fichier);
            try {
                this.imp.rapport = await api("/import?simulation=" + (simulation ? 1 : 0), { method: "POST", body: fd });
                if (!simulation) {
                    this.notifier(`${this.imp.rapport.crees + this.imp.rapport.mis_a_jour} matériel(s) importé(s)`);
                    this.charger();
                }
            } catch (e) {
                const d = Object.values(e.details || {}).join(" ");
                this.notifier(d || e.message, "danger");
            }
            this.imp.enCours = false;
        },

        // ---------- Pannes ----------
        ouvrirPanne(m) {
            this.panne = { materiel_id: m.materiel_id, libelle: `${m.type} ${m.modele} — ${m.numero_serie}`,
                           date_panne: this.aujourdhui, description: "" };
            bootstrap.Modal.getOrCreateInstance("#modalPanne").show();
        },
        async enregistrerPanne() {
            try {
                await api(`/materiels/${this.panne.materiel_id}/pannes`, jsonOpts("POST", this.panne));
                bootstrap.Modal.getInstance("#modalPanne").hide();
                this.notifier("Panne enregistrée");
            } catch (e) { this.notifier(e.message, "danger"); }
        },

        // ---------- Maintenance prédictive ----------
        async ouvrirMaintenance() {
            this.tab = "maintenance";
            try {
                [this.modele, this.predictions] = await Promise.all([
                    api("/maintenance/modele"),
                    api("/maintenance/predictions").then(r => r.items),
                ]);
            } catch (e) { this.notifier(e.message, "danger"); }
        },
        async entrainer() {
            this.entrainement = true;
            try {
                await api("/maintenance/entrainer", { method: "POST" });
                this.notifier("Modèle entraîné");
                await this.ouvrirMaintenance();
            } catch (e) { this.notifier(e.message, "danger"); }
            this.entrainement = false;
        },
    },
    async mounted() {
        this.charger();
        try { this.health = await api("/health"); } catch (e) { /* ignore */ }
    },
}).mount("#app");
