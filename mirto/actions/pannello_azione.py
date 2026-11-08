# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marco Dalicco
"""Le regole della finestra «Azione del nodo», scritte fuori dalla finestra.

Stanno qui per lo stesso motivo di ``pannello_audio.py``: le finestre non si possono eseguire dove
questo codice viene scritto, e queste regole decidono cosa un utente vede — sbagliarle non dà un
errore, dà una schermata che non si capisce.

Il 22/10, guardando la finestra finita, sono venute fuori due cose storte:

* **la sorgente non si sceglieva.** Gli obiettivi — cosa fa scattare l'azione — mescolavano in un
  elenco solo le cose viste dalla camera (persona, gatto, cane…) e quelle sentite dal microfono
  (vetro rotto, cane che abbaia). Chi guardava dava per scontato «è la camera», e non c'era niente a
  dirgli il contrario;
* **si vedevano scelte che non servivano.** «Solo per le camere» ha senso se a far scattare l'azione
  è una camera; su un'azione che scatta per un **suono** è una riga da ignorare, e una riga da
  ignorare è una riga che fa dubitare di aver capito. Ora la sorgente in cima decide cosa compare, e
  quello che non serve **sparisce**, non resta spento.

Una cosa importante sul modello: la sorgente **non è un campo salvato**. Gli obiettivi la portano già
con sé — un obiettivo che comincia per ``sound:`` è un suono, gli altri sono roba vista — quindi si
ricava da quelli quando la finestra si apre e si applica filtrando l'elenco. Nessun campo nuovo,
niente da migrare, e nessuna possibilità che le due cose dicano il contrario l'una dell'altra.
"""

from __future__ import annotations

from ..audio.classes import SOUND_PREFIX
from ..i18n import _

VIDEO, AUDIO, ENTRAMBI = "video", "audio", "entrambi"
SORGENTI = (VIDEO, AUDIO, ENTRAMBI)


def sorgente_di(chiave: str) -> str:
    """Da dove arriva un obiettivo: ``"audio"`` per un suono, ``"video"`` per tutto il resto."""
    return AUDIO if str(chiave).startswith(SOUND_PREFIX) else VIDEO


def sorgente_da_obiettivi(obiettivi) -> str:
    """La sorgente di un'azione già salvata, ricavata da cosa la fa scattare.

    Senza obiettivi si risponde ``"video"``: è il caso di un'azione nuova, e una camera è quello che
    tutti hanno. Nessun obiettivo salvato non vuol dire «nessuna sorgente», vuol dire «non ancora
    scelto».
    """
    viste = {sorgente_di(o) for o in (obiettivi or [])}
    if not viste or viste == {VIDEO}:
        return VIDEO
    return AUDIO if viste == {AUDIO} else ENTRAMBI


def obiettivi_per_sorgente(opzioni, sorgente: str) -> list:
    """Gli obiettivi da mostrare: ``opzioni`` è ``[(chiave, testo)]``, filtrata per sorgente."""
    if sorgente == ENTRAMBI:
        return list(opzioni)
    return [(k, t) for k, t in opzioni if sorgente_di(k) == sorgente]


def obiettivi_da_tenere(scelti, sorgente: str) -> set:
    """Cosa resta scelto cambiando sorgente: solo quello che la sorgente nuova può far scattare.

    Cambiare da «audio» a «video» lasciando spuntato un suono vorrebbe dire un'azione che scatta per
    una cosa che la finestra non mostra più: invisibile e viva, il peggio dei due mondi.
    """
    if sorgente == ENTRAMBI:
        return set(scelti or [])
    return {o for o in (scelti or []) if sorgente_di(o) == sorgente}


def serve_scegliere_le_camere(sorgente: str) -> bool:
    """«Solo per le camere» ha senso solo se a far scattare l'azione c'è una camera."""
    return sorgente != AUDIO


def camere_da_mostrare(camere, scelte) -> list[dict]:
    """Le camere proponibili: quelle **accese** nella griglia, più quelle già scelte da questa azione.

    Una camera che c'è ma è spenta non è utilizzabile: prima va accesa dalla griglia delle camere, e
    offrirla qui vorrebbe dire far configurare un'azione che non scatterà mai (22/10).

    Ma una camera già scelta e poi spenta si mostra lo stesso, segnata come spenta: toglierla
    dall'elenco in silenzio vorrebbe dire cambiare la configurazione di un'azione senza dirlo a
    nessuno, e lasciare chi guarda convinto di una cosa falsa.
    """
    scelte = {str(s) for s in (scelte or [])}
    fuori, visti = [], set()
    for c in (camere or []):
        nome = str((c or {}).get("name") or "")
        if not nome or nome in visti:
            continue
        accesa = bool((c or {}).get("enabled", True))
        if accesa or nome in scelte:
            visti.add(nome)
            fuori.append({"nome": nome, "accesa": accesa})
    for nome in sorted(scelte - visti):     # scelta, e adesso sparita del tutto dal computer
        fuori.append({"nome": nome, "accesa": False})
    return fuori


def fonti_da_mostrare(camere, microfoni, scelte) -> tuple[list, list]:
    """Le due liste da mostrare sotto il trigger: ``(camere, microfoni)``.

    Si filtra per **nome**, e il programma ci arriva già: un evento audio porta il nome del microfono
    nello stesso campo in cui un evento video porta quello della camera, e il filtro guarda id e nome.
    Quindi non serve un campo nuovo in un'azione — serve solo mostrare la lista giusta.

    Le scelte che non si riconoscono (una camera tolta dal computer, un microfono staccato) restano
    fra le camere, che è dove stavano prima: devono comunque comparire da qualche parte, e comparire
    in **tutte e due** le liste vorrebbe dire salvarle due volte.
    """
    scelte = {str(s) for s in (scelte or [])}
    nomi = [str((m or {}).get("name") or "") for m in (microfoni or [])]
    nomi = [n for n in dict.fromkeys(nomi) if n]
    mic = [{"nome": n, "accesa": True} for n in nomi]
    return camere_da_mostrare(camere, scelte - set(nomi)), mic


# I testi delle (i) accanto a ogni etichetta. Stanno qui, tutti insieme, per due motivi: si leggono
# di fila come se fossero un manuale (e si scopre subito quale è scritto male), e una prova può
# pretendere che ogni campo della finestra ne abbia uno.
def aiuto(chiave: str) -> str:
    """Il testo della (i) accanto a un'etichetta. Vuoto se quel campo non ne ha uno."""
    return _AIUTI().get(chiave, "")


def chiavi_aiuto() -> tuple:
    """Le chiavi per cui esiste un aiuto: le usa la prova che nessun campo resti senza."""
    return tuple(_AIUTI())


def _AIUTI() -> dict:  # noqa: N802
    """Costruito a ogni chiamata, non all'import: la lingua si sceglie dopo, e un testo tradotto una
    volta sola resterebbe nella lingua di quel momento per sempre."""
    return {
        "nome": _('Come si chiama questa azione nelle notifiche e nel registro. Serve a te per '
                  'riconoscerla: il nodo non lo guarda.'),
        "tipo": _('Decide le regole di sicurezza: una sirena non può scattare per un animale, un '
                  'dissuasore non può scattare per una persona. Il tipo non si aggira.'),
        "comando": _('Quale comando esegue il nodo. L\'elenco è quello che il nodo dichiara di saper '
                     'fare: se un comando non c\'è, è il nodo che non ce l\'ha.'),
        "cosa_fa": _('I pezzi che l\'azione esegue, in fila: accendi una luce, aspetta, suona tre '
                     'volte. Il «+» propone solo quello che i dispositivi collegati sanno fare '
                     'davvero — ogni nodo con i suoi comandi e le sue casse — e il pulsante «Prova» '
                     'la esegue adesso, prima di salvarla.'),
        "quando_attiva": _('Tre scelte, e cambiano le regole.\n\n'
                           '«Solo quando la casa è protetta» è l\'antifurto: scatta a casa inserita, '
                           'per le persone riconosciute con certezza, e non ha limiti d\'uso — se c\'è '
                           'qualcuno in casa deve suonare.\n\n'
                           '«Sempre» è per le cose che hanno senso anche a casa aperta: il campanello, '
                           'il dissuasore per gli animali, un avviso. In cambio ha i limiti: pausa fra '
                           'due scatti, tetto giornaliero, fascia oraria.\n\n'
                           '«Prank» è lo scherzo. Non ha nessuno di quei limiti ed è l\'unica che può '
                           'scattare per una **persona** con un dissuasore. In cambio le servono due '
                           'cose insieme: la modalità Prank accesa su quel nodo — la accendi tu in '
                           'Nodi → Configurazione → Modalità Prank, dura quello che scegli, scade da sé e «Ferma '
                           'tutto» la spegne — e la casa **non** protetta. Appena inserisci '
                           'l\'antifurto gli scherzi tacciono: da quel momento quello che suona vuol '
                           'dire qualcosa, e un falso allarme sul telefono di chi non è in casa non è '
                           'uno scherzo. Fuori da lì non parte mai, e i limiti del nodo (30 secondi '
                           'per scatto) restano comunque.'),
        "quando_parte": _('Subito, oppure solo dopo una conferma. La conferma esiste per le cose che '
                          'disturbano i vicini: si premono da Telegram.'),
        "ritardo": _('Quanto aspetta il nodo prima di eseguire, da quando l\'evento è confermato. '
                     'Zero vuol dire subito.'),
        "attesa_conferma": _('Quanto resta in attesa di un sì prima di rinunciare. Scaduta, decide la '
                             'riga qui sotto.'),
        "se_nessuno_conferma": _('Cosa succede se nessuno risponde in tempo. «Esegui comunque» vale '
                                 'solo quando la persona è riconosciuta con certezza.'),
        "obiettivi_camera": _('Cosa devono riconoscere le camere spuntate qui sopra perché l\'azione '
                              'parta. Più cose spuntate = scatta per una qualunque di quelle, non per '
                              'tutte insieme — e vale per tutte le camere scelte, non una per camera. '
                              'Dentro ogni riquadro acceso, «AI» dice quanto dev\'essere sicuro il '
                              'modello **per quella cosa**: «standard» segue la soglia generale anche '
                              'dopo un aggiornamento del modello, un numero scritto lì vale solo per '
                              'quest\'azione e resta quello.'),
        "obiettivi_microfono": _('Che suoni devono riconoscere i microfoni spuntati qui sopra. Come '
                                 'per le camere: una cosa qualunque fra quelle, su uno qualunque dei '
                                 'microfoni scelti. Anche qui «AI» dentro il riquadro regola quanto '
                                 'sicuro dev\'essere il riconoscimento — e qui giudica il modello dei '
                                 'suoni, che ha già una sua soglia per ogni tipo di suono: quella che '
                                 'scrivi si somma, cioè chiede **di più**, non di meno.'),
        "suoni_emessi": _('Quali suoni il nodo **emette** quando scatta — non sono quelli che lo fanno '
                          'scattare. Nessuno spuntato = tutti quelli che ha, a rotazione, perché gli '
                          'animali si abituano in fretta a un suono solo.'),
        "pausa": _('Quanto deve passare prima che questa azione possa riscattare. Serve a non '
                   'trasformare un animale di passaggio in un disturbo continuo.'),
        "massimi": _('Quante volte al giorno al massimo. Raggiunto il tetto, l\'azione sta zitta fino '
                     'a mezzanotte.'),
        "fascia": _('Solo dentro questa fascia oraria, per esempio 06:00-20:30. Vuota = sempre. '
                    'Controlla il regolamento del tuo Comune sugli orari di quiete.'),
        "avvisa": _('Manda un messaggio su Telegram ogni volta che scatta, con la foto se c\'è.'),
        "camere": _('Da quali camere. Nessuna spuntata = tutte. Più camere spuntate vogliono dire '
                    '**oppure**: scatta quella che vede per prima, e cercano tutte la stessa cosa.'),
        "microfoni": _('Da quali microfoni. Nessuno spuntato = tutti, e come per le camere vale '
                       '**oppure**: basta che uno senta.'),
        "attiva": _('Spenta, l\'azione resta scritta ma non scatta. Utile per provare senza '
                    'cancellare niente.'),
    }


def manca_negli_input(camere_spuntate, microfoni_spuntati, visti, sentiti,
                      scegliibili: bool = True, nuova: bool = True) -> str:
    """Cosa manca nella schermata «cosa la fa scattare». Vuoto = si può andare avanti.

    Marco, 24/10: *«ho selezionato i microfoni e sono riuscito ad andare avanti senza selezionare un
    evento per i microfoni»*. Il controllo di prima chiedeva **almeno un riconoscimento in tutto**:
    con una camera e un microfono spuntati bastava dire cosa doveva vedere la camera, e il microfono
    restava lì, scelto e muto — un'azione che sembra ascoltare e non ascolta.

    La regola giusta è per input: ogni cosa che hai spuntato deve avere qualcosa da riconoscere. E
    prima ancora, qualcosa dev'essere spuntato: senza, non c'è niente che possa far scattare niente.
    """
    if not camere_spuntate and not microfoni_spuntati:
        if not scegliibili:
            return ""   # non c'è niente da spuntare: chiederlo sarebbe un vicolo cieco
        if not nuova and (visti or sentiti):
            # Azione già salvata: nessuna fonte spuntata vuol dire «tutte», ed è il significato che
            # quel campo ha sempre avuto. Chiederle di sceglierne una le cambierebbe il senso.
            return ""
        return _('Scegli almeno una camera o un microfono: è da lì che arriva quello che fa '
                 'scattare l\'azione.')
    if camere_spuntate and not visti:
        return _('Hai scelto una camera ma non hai detto che cosa deve riconoscere.')
    if microfoni_spuntati and not sentiti:
        return _('Hai scelto un microfono ma non hai detto che suono deve riconoscere.')
    return ""


def si_vede_la_griglia(spuntati, scelti, nuova: bool = False) -> bool:
    """La griglia dei riconoscimenti si vede solo se quell'input è stato scelto.

    *«Se non seleziono una cam o un microfono non si devono vedere le possibili associazioni di
    riconoscimento AI»*: chiedere cosa deve riconoscere una camera che non hai scelto è la stessa
    domanda fuori posto di tutte le altre volte.

    L'eccezione è un'azione già salvata che ha dei riconoscimenti e nessuna fonte spuntata — vuol
    dire «tutte», ed è il significato che quel campo ha sempre avuto: nasconderle la renderebbe
    impossibile da modificare.

    Un'azione nuova nasce con «Persona» già spuntata — una comodità, non una scelta fatta — e quindi
    per lei conta solo quello che hai spuntato davvero: la griglia compare quando scegli la camera, e
    «Persona» è lì che ti aspetta.
    """
    return bool(spuntati) or (bool(scelti) and not nuova)
