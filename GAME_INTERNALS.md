# Shadow of the Colossus (SCUS-97472, NTSC-U v1.00) — how the game works

Resultado de decompilar o jogo com `tools/decompile.py` (Ghidra headless + plugin Emotion Engine, símbolos de
`work/symbols.tsv`). A decompilação bruta fica em `work/internals/*.c` (local, não vai para o git); este arquivo é
o resumo. Endereços valem só para esta versão. Complementa `MULTIPLAYER.md` (achados de rede/co-op).

Regenerar: `python tools/decompile.py import RAM.bin` (RAM de um savestate) e depois
`python tools/decompile.py NOME|0xENDEREÇO ... > saida.c`.

## 1. Arquitetura geral

* `SCUS_974.72` é só um carregador (0x100000-0x16A398). O jogo vive em módulos **XFF** (`GAMECORE`, `KERNEL`,
  `MANAGER`, `STARTUP`) relocados em tempo de execução para 0x01100000-0x01500000. As tabelas de símbolos ficam
  na RAM (6477 nomes). Os módulos se ligam por símbolo (`isysResolveOneModuleObj`, `_DldSysUndefSymbol`).
* Símbolos por área (funções): SCR/scr 592 (comandos de script), nico_* 268 (scripts de personagem), GAMEUTIL 230,
  CAM 85, charrootdyn 84 (física/raiz do personagem), AI 80 (IA dos colossos), gStat 41 (estado do jogo), isys 40
  (kernel de objetos/threads), doit_* 33 (rotinas por frame), motrootdyn 31 (movimento por animação).
* Concentração de código: 0x0134xxxx (jogo/estado/player/stage), 0x0125xxxx (dano/letras), 0x0145xxxx (IA de
  script), 0x0118-0x011bxxxx (gráfico, personagem), 0x0140-0x0141xxxx (câmera), 0x001bxxxx (kernel, iosMalloc).
* Heap principal: partição `0x001E0C88` (0x00290FFC-0x01FE0000, ~30 MB, quase tudo ocupado). Livre real no
  santuário ~380 KB; um Wander clone custa ~250 KB, um cavalo ~180 KB. Alocador de blocos com listas livres por
  classe de tamanho (`iosMallocAlign` 0x001AFFD8 -> núcleo 0x001ADDC0; `iosFree` 0x001B0328). Sem caminho de
  falta de memória: falha = mensagem `_iosMallocAlign: memory over ptn ...` e travamento (`iosJumpRecoverPoint`).

## 2. Kernel de objetos e threads (`isys*`)

* Todo objeto do jogo é um **isys object** com byte de tipo em `+0x08` (6/9/0xB/0xC/0xD/0xE/0x16... = objetos
  com subthread própria; 9 = personagem; 0x16 = personagem alternativo; 0xB = script). Criação:
  `_isysCreateObj(heap, tipo, tamanho, ..., tabela_de_métodos)`.
* **Grupos** (`isysGroupForFirst/Next/Exit`, `isysAddGroupWithLinkParam`): listas encadeadas de nós de 0x18 bytes
  na tabela a partir de 0x0021B900, protegidas por um semáforo recursivo (0x0026D98C). Grupos globais usados pelo
  jogo: 0x01465940 = jogador, 0x01465944 = cavalo, 0x01465948 = colosso. `SCRGetFirstObj(grupo)` pega o primeiro.
* **Subthreads cooperativas**: cada objeto roda um script como co-rotina (`isysStartSubThread`,
  `isysJumpSubThread`, `isysSleepSubThread`). `scrRun()` (0x013CF9E8) cede a vez; é o "yield" de tudo
  (cutscenes, eventos, IA). Não há preempção: se um script não chama `scrRun`, o jogo inteiro para.
* **Exclusão**: `isysRequestDeleteObj` chama o handler de `obj+0x18` (e o zera), marca o objeto
  (`+0x08 |= 0x10000`) e o encadeia numa lista de exclusão pendente (0x0026D988); a limpeza real vem depois.
* **Cartas (letters)**: mensagens entre objetos (`create_letter`/`send_letter`); dano é uma carta
  (`SendDirectDamageLetter` 0x01260C40 monta uma carta de 0x60 bytes: dano, atacante, geometria do golpe).
* **Interrupções de script** (`scriptIntrSignal` 0x011FD008): estruturas `ScrIntrDef_*` (0x01566BEC...) com um
  grupo de scripts que esperam; sinalizar faz cada um deles pular para o handler registrado
  (`scriptIntrEntryJump`). Exemplos: DEAD_BOSS, SCREEN_FADEIN/OUT, GAME_NEWSTART/LOADSTART/CANCEL/ENDING,
  MENU_PAUSE/CONTINUE, JUMP_HOKORA/SEKIBAN, DEBUG_CHECKPOINT_LOAD.
* Acesso por classe: um objeto tem várias "classes" embutidas: `GetCharacterClass` (obj+0x90), `GetLayoutClass`,
  `GetAnimationClass`, `GetDamageClass`, `GetScriptClass` (obj+0x20).

## 3. Loop de frame

* Quadro lógico: o jogo roda 60 quadros/s (contador do hook cresce +30/s só porque o hook roda 1x a cada 2).
  `graphFrameInit` (0x01189F68, chamado de 0x0134637C) abre o quadro e também roda `InitLayoutObjMatrix`;
  `graphFrameFinish` (0x01189C58, de 0x013463F8) fecha. Entre eles correm as rotinas `doit_*`
  (câmera, partículas, vento, expressões, IK de pescoço/movimento) e as subthreads dos objetos.
* Pausa: `PAUSE_FLAGS` 0x0146583C (menus, mapa, game over); modo de jogo `gameModeSet/Cls` em 0x01465D70
  (bit 0 = cutscene/menu). Em pausa/loading as threads de personagem não rodam.
* Pad: `padManagerData` 0x01287A00 (segurado, +8 push, +0xC solta, +0x10 repetição; analógico em +0x54).
  `GAMEUTIL_is_push_or_down_1p_button(modo, bit)` lê esses bits.

## 4. Dados do jogo, flags, progresso

* **Flags e variáveis**: `_gameFlagSet/Cls/Chk` (bitmap em 0x012D9B50), `_gameWorkSet/Chk` (inteiros),
  `_gameFWorkSet/Chk` (floats), `_gameIWorkSet/Chk`. Exemplos: work 8 = índice do colosso ativo; fwork 0xB = HP
  máximo (teto 570: `addPlayerMaxHp`). HP atual em 0x012DE264 (100 = cheio), garra em 0x012DE260.
* **Colossos**: tabela `SH_BossStatusDEFAULT` -> +0x10 = 16 registros de 0xEC bytes (+0x18 flag "vivo",
  +0x1C flag de boot, +0x50 flags, +0x51 área, +0x64/68/6C ponto de chegada relativo ao centro da área,
  +0x70 direção, +0xBC HP máx., +0xC0 garra máx.). `bossStatSetActiveBoss` liga só o layout do colosso N
  (flags), grava o índice (work 8), o contador de vitórias e a flag "vivo". `bossStatSetDeadBoss/ChkDeadBoss`
  (Chk = 1 quando MORTO). `bossStatStartDemo` (0x01349AD8) é o gatilho do encontro (BootBoss): teleporta o
  cavalo, liga as nuvens e inicia a cutscene (`gameModeSet(1)`).
* **Save**: `gStatMcSave/McLoad` (memory card), `gStatCheckPointSave/Load` (checkpoint em RAM: copia
  `gameFlagCheckPointLoad`, `backStageCheckPointLoad`, logs de sprites). Morrer = `gameflowGameOver` (flag) ->
  menu -> `gStatCheckPointLoad`/`gStatRestartPlayerChara` (reinstancia segmentos mas mantém os assets).
* Dificuldade: `getGameHardMode` -> `SetDamageClassDamageTableType`.

## 5. Mundo e estágios

* O mundo é dividido em **segmentos de estágio** (`SH_StageDataDEFAULT`, 0x14 bytes por registro,
  contador de referência em +0x10). `stagemanager_loadstagecore(heap, segmento, ...)` incrementa o contador;
  no 1º uso pede o arquivo ao gerenciador de folhas (`sheetMgrLoadSegmentIndex`), espera com `scrRun()` e chama
  `initstage` -> `initlayout`. `unloadstagecore` decrementa e libera no zero; `restartstagecore` só
  reinstancia (usado ao morrer).
* `initlayout` (0x01360548) percorre as entradas de layout (0x4C bytes cada): cada entrada aponta uma definição
  (`+4`), tem dependência (`+0xC`) e guarda o objeto criado em `+0x48`. Passada 1: cria as sem dependência
  (`0x013600A8`, que chama `loadObj`); passada 2: **laço até todas existirem** — se o pai nunca nasce (cache
  `definição+8` zerado, ou falta de memória) o laço é infinito. Esse foi o travamento pós-colosso.
* Segmentos do jogador: 0xB6 (Wander + bainha), 0xB8 (cabelo), 0xB7 e 0xB9 (objetos alias que desenham corpo),
  0xBA (cavalo + ajudante), 0xC3 (acessórios carregados após matar um colosso). Variáveis em 0x01465E00-E14.
* **Mundo contínuo (seamless)**: o terreno é carregado em volta do jogador (`InitSeamlessStageLoad`,
  `UpdateSeamlessStageLoad`, `getSeamlessStageCenter`, `YattsukeGetSeamlessAreaCenter`). Teleportar longe não
  traz o terreno sozinho: segure o jogador no ar ~15-20 s no ponto. A arena de um colosso é um segmento que só
  carrega quando se entra pela entrada: teleportar para dentro dela cai no vazio (y -300 = morte).
* Queda: o dano de queda usa o ponto de início da queda (`classe+0x10B0`), zerável com
  `ResetCharacterClassFallDownInfo` (0x011BB4B8).

## 6. Personagens, animação, física

* Objeto de personagem (classe 0x01206DD8 em `+4`, tipo 9): `classe = obj+0x90`; posição em `obj+0xA0`;
  tipo/"kind" em `classe+0x370` (2 = Wander, 3 = Agro, 4 = colosso, 5 = pássaros, 11 = Mono/acessórios).
* **Animação**: `classe+0x1C0` = movimento atual (ponteiro para descritor; word +8 = offset no arquivo de
  animação, igual em todas as máquinas), `+0x1C8` frame, `+0x1CC` frame anterior, tabela `+0x2AC` (0xDC bytes por
  entrada para o Wander). Funções: `RequestCharacterClassMotionChange`, `ForceAcceptMotion`, `InitAnimationClass`,
  `ExecObjectAnimation`, `SetCharacterClassMotionAddSyncMode`. O movimento do corpo vem da animação (root motion):
  `ExecCharaGeometry` (0x011B9C70) -> `GetCharaRootDynamics` (0x011D4340) -> `CalcRootFitting`. Por isso gravar a
  posição direto não funciona; só `DebugSetObjectGeometry` (0x01341218) define a base.
* **Jogador** (`nico_player1`, 0x013B2248): laço eterno por quadro: atualiza histórico de movimento (`MVHIST`),
  controle de colisão (`ColCont`), lê o botão de contexto (`GetPlayerControlButtonContext`), e despacha para os
  scripts de ação (`SCRPlayer*`: pegar item, chamar cavalo, ataque carregado, dano, sons de espada,
  `SCRPlayerFullPowerAndHp`). O cabelo/capa seguem o corpo (`nico_parentSync`).
* Cavalo: `gStatLoadHorseChara/Unload/Reload`, `gameflowChangeHorse`; o jogador é "montado" por um vínculo de
  objetos (`nico_GetPlayerCharacterRidingHorseObject`).

## 7. Colossos (IA)

* Um colosso é um personagem comum (kind 4) cujo script é o laço genérico de chefe:
  `SCRExecBossPreProcess` (0x01377740) e `PostProcess` (0x013790E0) por quadro, e a IA em bytecode interpretado
  por `updateScriptDataSystem` (0x01453E48: percepção, raiva, olhos, arco/flecha...). A IA só acorda quando o
  evento do arena roda `BootBoss`.
* Estado (`work = *GetSCRCharaAIWorkPtr(obj)`): +0x40 vida máx., +0x44 vida atual, +0x54 nº de partes,
  +0x10 definições das partes (0x58 bytes: +0 id, +0x1C máx), +0xE8 bloco de dano (float por parte em
  `+0x24 + 8*i`), +0xEC flag "morte já tratada". `getBossPartsLifeRatio` = (máx - dano)/máx.
* Golpe: carta de dano -> `PreProcess` acha a parte pelo atributo de superfície, soma dano, recalcula vida =
  máx - dano. Vida <= 0 (cauda 0x013776C0): vida = 0, `LayoutClass+0x1AC = 1`, `playerEnableDeath = 0`
  (0x01465E88) e **sinaliza `DEAD_BOSS`**. O script de espera do arena (`st_boss_status`, 0x013D0C20) salta para
  `st_boss_dead` (0x013D0588): trava pausa, espera a animação de morte, carrega o segmento do desfecho, cria
  os tentáculos (`CreateDeathTentacle`; poucos se a memória livre < 1 MB), +4 HP máx., e carrega o santuário.
  As animações de morte são carregadas sob demanda por `checkDeadMotionLoadByScriptDataSystem`.
* Atalhos que NÃO funcionam: `setBossAbsoluteDeath` (+0x48), só encher o dano, ou o comando 0x01370000
  (marca +0xEC sem carregar a animação -> "motion address is NULL: ..._death_front_loop.anb").

## 8. Câmera e UI

* Câmera por "cameraman" (`doit_cameraman`, `doit_game_camera`, 85 funções CAM*): regiões de câmera,
  câmera de chefe (`gameflowBossEyeCameraDisable`), parâmetros alocados a cada início de luta
  (`reset_game_camera_param_update_data_manager`, 0x65C bytes por entrada: foi a alocação que falhou na luta).
* Diálogos/menus: janelas próprias (`createYesNoDialog`, `windowSetCursorPos`...), não leem o pad pelo mesmo
  caminho dos botões de jogo; confirmar = bit 0x40, voltar = 0x10 (medido); cursor não foi achado.

## 9. O que isso significa para o co-op (ligações)

* Clonar objetos = repetir `initstage` sobre os segmentos do jogador, com alocador zerando blocos; o jogo não
  sabe dos clones, então o `guard` apaga antes de `unloadstagecore` e restaura o cache da definição.
* Memória é o gargalo (heap quase cheio): `heapgrow` doa RAM; toda checagem de ponteiro de objeto precisa de `in_heap`.
* Sincronizar colosso = pose (movimento+frame+raiz) + vida; matar = sequência do golpe final; progresso = flags
  de colosso mortos + índice ativo + HP máx.

## 11. Cutscenes (demo manager) � decompilado depois

* Uma cutscene � um **objeto-script `demo_common`** (0x0140DEF0) com parti��o de heap pr�pria (`iosCreatePartition`,
  id 0x87) e um bloco de estado de 0x54 bytes apontado por `ScriptLayoutClass+0x2D8`. Layout do bloco: +0x00 estado
  (6 = carregada), +0x04 pedido de boot, +0x08 boot conclu�do, +0x0C "iniciar", +0x10 "terminar", +0x20/+0x2C
  ids de scripts de fim, +0x44 j� come�ou, +0x48 id da cutscene, +0x4C/+0x50 progresso de carregamento.
* API (todas s� mexem nesse bloco; o trabalho real � feito pelo script `demo_common` que fica em la�o `scrRun`):
  `demoManBootRequest` (0x0140E330, liga o clipping pr�ximo desligado e pede boot), `demoManBootChk`,
  `demoManStart(obj, id)` (0x0140E390: grava o id e zera flags), `demoManChkStart` (+0x44 != 0),
  `demoManChkLoadFinish`, `demoManEnd` (0x0140E400: cria scripts de fim, cancela legendas),
  `demoManSkip` (0x0140E4F0: pula a c�mera via `SkipCutDemoCameraOrient`).
* `gameModeSet(mascara)` (0x01352B70) � **contado por refer�ncia**: o OR vai em `___gameMode` (0x01465D70) e cada bit
  incrementa um byte em 0x012DA4F0+bit; `gameModeCls` decrementa e s� limpa o bit no zero. Bit 0 = cutscene/menu.
  Isso explica por que duas cutscenes aninhadas n�o soltam o modo cedo demais.
* Para o co-op: o ponto limpo para uma barreira de cutscene gen�rica � `demoManStart` (id em +0x48 do bloco), igual ao
  que `barrier.py` j� faz em `bossStatStartDemo`; o fim � observ�vel por `___gameMode & 1` voltar a 0.

## 12. IA dos colossos: � dirigida por dados, n�o bytecode

(Corre��o da se��o 7: `updateScriptDataSystem` 0x01453E48 n�o interpreta bytecode; � um controlador por tipo,
alimentado por tabelas de fun��es com argumentos.)

* `work = GetSCRCharaAIWorkPtr(obj)`; `work+0x4090` = tipo do controlador: 0 pernas r�pidas (`updateFastLegCtrlWork`),
  1 pernas lentas, 2 p�ssaro (`updateBirdCtrlWork`), 3 minhoca/serpente (`updateWormCtrlWork`), 4 lento+p�ssaro,
  6 outro, 7 cavalo. Estado em `work+0x3D10` (+0x3DC0 para a parte de p�ssaro). Alvo = jogador
  (`SCRGetFirstObj(0x01465940)`, guardado em `work+0x4078`). Por quadro tamb�m: `updatePerceptionWork` (vis�o/
  audi��o), `updateAngryWork` (raiva), `updateEyeWork`, `updateArrowThrustWork`, `updateHintWork`, `setSCRGazeStatus`.
* **Raiva** = float em `work+0x40F8`, limitado a 0..100: decai por quadro quando n�o est� tomando dano e sobe ao
  levar golpe, conforme o tipo de dano (`DamageClass+0x60`: 0/5 usa par�metro +8, 1/2/3/4 usa +4).
* **Tabelas de comportamento**: o estado atual � um registro (`work+0x409c`) que aponta par�metros de movimento
  (`work+0x40A0`) copiados para os controladores (`setDashFastLegCtrlWork`, `setBackFastLegCtrlWork`,
  `setRunStopTurnFastLegCtrlWork`, `setAttainModePathWork`...) e listas de a��es e condi��es:
  * **a��o** (`execSCRBaseFunc` 0x01455FE8): lista de registros de 0x40 bytes `{fun��o, arg...}` terminada em
    fun��o nula; chama cada fun��o com os argumentos do registro.
  * **condi��o** (`execCondition` 0x01455AE0): registros de 0x50 bytes `{modo, esperado, fun��o, args...}`; modo 1 =
    todas devem retornar o esperado (E), modo 2 = qualquer uma (OU); encadeia at� o pr�ximo ponteiro nulo.
  As fun��es apontadas s�o os comandos `SCR*` (592 deles) e os dados v�m dos m�dulos (`SH_*`), por isso a IA de
  cada colosso � um conjunto de tabelas reloc�veis, n�o c�digo.

### Colossos que n�o aparecem ao teleportar (7, 10, 13, 14, 15) � resultado negativo

* Dump dos 16 registros (`+0x50` flags, `+0x51` �rea, `+0x1C` flag de boot, `+0x24` GObject, `+0x34/+0x38`
  segmentos, `+0xB0`, `+0xE8`): nenhum campo separa os 5 que faltam dos 11 que carregam (flags 0x4207/0x0F07/0x1607/
  0x1007/0x5502 vs 0x3707, 0x2306...). Para os faltantes `bossStatBootChk` (0x0134A458) devolve -1 (o GObject em
  `record+0xE8` n�o est� registrado) e o heap livre fica ~800-950 mil palavras (quase nada carregou). Esperar 150 s
  com o jogador parado no ponto de chegada n�o muda nada (n�o � demora de streaming), e n�o existe nenhum
  personagem tipo 4 em nenhum lugar da RAM. Pr�xima hip�tese: o GObject desses nasce por um gatilho de evento/
  posi��o fora do ponto de chegada (o #4 j� aparecia a 641 m dele). Ainda n�o testado.

## 10. Lacunas (ainda não decompilado a fundo)

* Os dados concretos das tabelas de IA de cada colosso (condi��es/a��es por estado) e o motivo de 5 colossos n�o carregarem (se��o 12).
* Sistema de cutscenes (`demoManStart`, `SDemo*`), áudio, memory card, física `charrootdyn`/`motrootdyn`.
* Formato dos arquivos (`sheetMgr*`, XFF/anb/lws) e do mapa do mundo (`SH_StageDataDEFAULT`).
* Por que 5 colossos (7, 10, 13-15) não aparecem ao teleportar (evento de spawn).
