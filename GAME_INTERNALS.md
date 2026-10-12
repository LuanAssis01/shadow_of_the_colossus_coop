# Shadow of the Colossus (SCUS-97472, NTSC-U v1.00) â€” how the game works

Resultado de decompilar o jogo com `tools/decompile.py` (Ghidra headless + plugin Emotion Engine, sÃ­mbolos de
`work/symbols.tsv`). A decompilaÃ§Ã£o bruta fica em `work/internals/*.c` (local, nÃ£o vai para o git); este arquivo Ã©
o resumo. EndereÃ§os valem sÃ³ para esta versÃ£o. Complementa `MULTIPLAYER.md` (achados de rede/co-op).

Regenerar: `python tools/decompile.py import RAM.bin` (RAM de um savestate) e depois
`python tools/decompile.py NOME|0xENDEREÃ‡O ... > saida.c`.

## 1. Arquitetura geral

* `SCUS_974.72` Ã© sÃ³ um carregador (0x100000-0x16A398). O jogo vive em mÃ³dulos **XFF** (`GAMECORE`, `KERNEL`,
  `MANAGER`, `STARTUP`) relocados em tempo de execuÃ§Ã£o para 0x01100000-0x01500000. As tabelas de sÃ­mbolos ficam
  na RAM (6477 nomes). Os mÃ³dulos se ligam por sÃ­mbolo (`isysResolveOneModuleObj`, `_DldSysUndefSymbol`).
* SÃ­mbolos por Ã¡rea (funÃ§Ãµes): SCR/scr 592 (comandos de script), nico_* 268 (scripts de personagem), GAMEUTIL 230,
  CAM 85, charrootdyn 84 (fÃ­sica/raiz do personagem), AI 80 (IA dos colossos), gStat 41 (estado do jogo), isys 40
  (kernel de objetos/threads), doit_* 33 (rotinas por frame), motrootdyn 31 (movimento por animaÃ§Ã£o).
* ConcentraÃ§Ã£o de cÃ³digo: 0x0134xxxx (jogo/estado/player/stage), 0x0125xxxx (dano/letras), 0x0145xxxx (IA de
  script), 0x0118-0x011bxxxx (grÃ¡fico, personagem), 0x0140-0x0141xxxx (cÃ¢mera), 0x001bxxxx (kernel, iosMalloc).
* Heap principal: partiÃ§Ã£o `0x001E0C88` (0x00290FFC-0x01FE0000, ~30 MB, quase tudo ocupado). Livre real no
  santuÃ¡rio ~380 KB; um Wander clone custa ~250 KB, um cavalo ~180 KB. Alocador de blocos com listas livres por
  classe de tamanho (`iosMallocAlign` 0x001AFFD8 -> nÃºcleo 0x001ADDC0; `iosFree` 0x001B0328). Sem caminho de
  falta de memÃ³ria: falha = mensagem `_iosMallocAlign: memory over ptn ...` e travamento (`iosJumpRecoverPoint`).

## 2. Kernel de objetos e threads (`isys*`)

* Todo objeto do jogo Ã© um **isys object** com byte de tipo em `+0x08` (6/9/0xB/0xC/0xD/0xE/0x16... = objetos
  com subthread prÃ³pria; 9 = personagem; 0x16 = personagem alternativo; 0xB = script). CriaÃ§Ã£o:
  `_isysCreateObj(heap, tipo, tamanho, ..., tabela_de_mÃ©todos)`.
* **Grupos** (`isysGroupForFirst/Next/Exit`, `isysAddGroupWithLinkParam`): listas encadeadas de nÃ³s de 0x18 bytes
  na tabela a partir de 0x0021B900, protegidas por um semÃ¡foro recursivo (0x0026D98C). Grupos globais usados pelo
  jogo: 0x01465940 = jogador, 0x01465944 = cavalo, 0x01465948 = colosso. `SCRGetFirstObj(grupo)` pega o primeiro.
* **Subthreads cooperativas**: cada objeto roda um script como co-rotina (`isysStartSubThread`,
  `isysJumpSubThread`, `isysSleepSubThread`). `scrRun()` (0x013CF9E8) cede a vez; Ã© o "yield" de tudo
  (cutscenes, eventos, IA). NÃ£o hÃ¡ preempÃ§Ã£o: se um script nÃ£o chama `scrRun`, o jogo inteiro para.
* **ExclusÃ£o**: `isysRequestDeleteObj` chama o handler de `obj+0x18` (e o zera), marca o objeto
  (`+0x08 |= 0x10000`) e o encadeia numa lista de exclusÃ£o pendente (0x0026D988); a limpeza real vem depois.
* **Cartas (letters)**: mensagens entre objetos (`create_letter`/`send_letter`); dano Ã© uma carta
  (`SendDirectDamageLetter` 0x01260C40 monta uma carta de 0x60 bytes: dano, atacante, geometria do golpe).
* **InterrupÃ§Ãµes de script** (`scriptIntrSignal` 0x011FD008): estruturas `ScrIntrDef_*` (0x01566BEC...) com um
  grupo de scripts que esperam; sinalizar faz cada um deles pular para o handler registrado
  (`scriptIntrEntryJump`). Exemplos: DEAD_BOSS, SCREEN_FADEIN/OUT, GAME_NEWSTART/LOADSTART/CANCEL/ENDING,
  MENU_PAUSE/CONTINUE, JUMP_HOKORA/SEKIBAN, DEBUG_CHECKPOINT_LOAD.
* Acesso por classe: um objeto tem vÃ¡rias "classes" embutidas: `GetCharacterClass` (obj+0x90), `GetLayoutClass`,
  `GetAnimationClass`, `GetDamageClass`, `GetScriptClass` (obj+0x20).

## 3. Loop de frame

* Quadro lÃ³gico: o jogo roda 60 quadros/s (contador do hook cresce +30/s sÃ³ porque o hook roda 1x a cada 2).
  `graphFrameInit` (0x01189F68, chamado de 0x0134637C) abre o quadro e tambÃ©m roda `InitLayoutObjMatrix`;
  `graphFrameFinish` (0x01189C58, de 0x013463F8) fecha. Entre eles correm as rotinas `doit_*`
  (cÃ¢mera, partÃ­culas, vento, expressÃµes, IK de pescoÃ§o/movimento) e as subthreads dos objetos.
* Pausa: `PAUSE_FLAGS` 0x0146583C (menus, mapa, game over); modo de jogo `gameModeSet/Cls` em 0x01465D70
  (bit 0 = cutscene/menu). Em pausa/loading as threads de personagem nÃ£o rodam.
* Pad: `padManagerData` 0x01287A00 (segurado, +8 push, +0xC solta, +0x10 repetiÃ§Ã£o; analÃ³gico em +0x54).
  `GAMEUTIL_is_push_or_down_1p_button(modo, bit)` lÃª esses bits.

## 4. Dados do jogo, flags, progresso

* **Flags e variÃ¡veis**: `_gameFlagSet/Cls/Chk` (bitmap em 0x012D9B50), `_gameWorkSet/Chk` (inteiros),
  `_gameFWorkSet/Chk` (floats), `_gameIWorkSet/Chk`. Exemplos: work 8 = Ã­ndice do colosso ativo; fwork 0xB = HP
  mÃ¡ximo (teto 570: `addPlayerMaxHp`). HP atual em 0x012DE264 (100 = cheio), garra em 0x012DE260.
* **Colossos**: tabela `SH_BossStatusDEFAULT` -> +0x10 = 16 registros de 0xEC bytes (+0x18 flag "vivo",
  +0x1C flag de boot, +0x50 flags, +0x51 Ã¡rea, +0x64/68/6C ponto de chegada relativo ao centro da Ã¡rea,
  +0x70 direÃ§Ã£o, +0xBC HP mÃ¡x., +0xC0 garra mÃ¡x.). `bossStatSetActiveBoss` liga sÃ³ o layout do colosso N
  (flags), grava o Ã­ndice (work 8), o contador de vitÃ³rias e a flag "vivo". `bossStatSetDeadBoss/ChkDeadBoss`
  (Chk = 1 quando MORTO). `bossStatStartDemo` (0x01349AD8) Ã© o gatilho do encontro (BootBoss): teleporta o
  cavalo, liga as nuvens e inicia a cutscene (`gameModeSet(1)`).
* **Save**: `gStatMcSave/McLoad` (memory card), `gStatCheckPointSave/Load` (checkpoint em RAM: copia
  `gameFlagCheckPointLoad`, `backStageCheckPointLoad`, logs de sprites). Morrer = `gameflowGameOver` (flag) ->
  menu -> `gStatCheckPointLoad`/`gStatRestartPlayerChara` (reinstancia segmentos mas mantÃ©m os assets).
* Dificuldade: `getGameHardMode` -> `SetDamageClassDamageTableType`.

## 5. Mundo e estÃ¡gios

* O mundo Ã© dividido em **segmentos de estÃ¡gio** (`SH_StageDataDEFAULT`, 0x14 bytes por registro,
  contador de referÃªncia em +0x10). `stagemanager_loadstagecore(heap, segmento, ...)` incrementa o contador;
  no 1Âº uso pede o arquivo ao gerenciador de folhas (`sheetMgrLoadSegmentIndex`), espera com `scrRun()` e chama
  `initstage` -> `initlayout`. `unloadstagecore` decrementa e libera no zero; `restartstagecore` sÃ³
  reinstancia (usado ao morrer).
* `initlayout` (0x01360548) percorre as entradas de layout (0x4C bytes cada): cada entrada aponta uma definiÃ§Ã£o
  (`+4`), tem dependÃªncia (`+0xC`) e guarda o objeto criado em `+0x48`. Passada 1: cria as sem dependÃªncia
  (`0x013600A8`, que chama `loadObj`); passada 2: **laÃ§o atÃ© todas existirem** â€” se o pai nunca nasce (cache
  `definiÃ§Ã£o+8` zerado, ou falta de memÃ³ria) o laÃ§o Ã© infinito. Esse foi o travamento pÃ³s-colosso.
* Segmentos do jogador: 0xB6 (Wander + bainha), 0xB8 (cabelo), 0xB7 e 0xB9 (objetos alias que desenham corpo),
  0xBA (cavalo + ajudante), 0xC3 (acessÃ³rios carregados apÃ³s matar um colosso). VariÃ¡veis em 0x01465E00-E14.
* **Mundo contÃ­nuo (seamless)**: o terreno Ã© carregado em volta do jogador (`InitSeamlessStageLoad`,
  `UpdateSeamlessStageLoad`, `getSeamlessStageCenter`, `YattsukeGetSeamlessAreaCenter`). Teleportar longe nÃ£o
  traz o terreno sozinho: segure o jogador no ar ~15-20 s no ponto. A arena de um colosso Ã© um segmento que sÃ³
  carrega quando se entra pela entrada: teleportar para dentro dela cai no vazio (y -300 = morte).
* Queda: o dano de queda usa o ponto de inÃ­cio da queda (`classe+0x10B0`), zerÃ¡vel com
  `ResetCharacterClassFallDownInfo` (0x011BB4B8).

## 6. Personagens, animaÃ§Ã£o, fÃ­sica

* Objeto de personagem (classe 0x01206DD8 em `+4`, tipo 9): `classe = obj+0x90`; posiÃ§Ã£o em `obj+0xA0`;
  tipo/"kind" em `classe+0x370` (2 = Wander, 3 = Agro, 4 = colosso, 5 = pÃ¡ssaros, 11 = Mono/acessÃ³rios).
* **AnimaÃ§Ã£o**: `classe+0x1C0` = movimento atual (ponteiro para descritor; word +8 = offset no arquivo de
  animaÃ§Ã£o, igual em todas as mÃ¡quinas), `+0x1C8` frame, `+0x1CC` frame anterior, tabela `+0x2AC` (0xDC bytes por
  entrada para o Wander). FunÃ§Ãµes: `RequestCharacterClassMotionChange`, `ForceAcceptMotion`, `InitAnimationClass`,
  `ExecObjectAnimation`, `SetCharacterClassMotionAddSyncMode`. O movimento do corpo vem da animaÃ§Ã£o (root motion):
  `ExecCharaGeometry` (0x011B9C70) -> `GetCharaRootDynamics` (0x011D4340) -> `CalcRootFitting`. Por isso gravar a
  posiÃ§Ã£o direto nÃ£o funciona; sÃ³ `DebugSetObjectGeometry` (0x01341218) define a base.
* **Jogador** (`nico_player1`, 0x013B2248): laÃ§o eterno por quadro: atualiza histÃ³rico de movimento (`MVHIST`),
  controle de colisÃ£o (`ColCont`), lÃª o botÃ£o de contexto (`GetPlayerControlButtonContext`), e despacha para os
  scripts de aÃ§Ã£o (`SCRPlayer*`: pegar item, chamar cavalo, ataque carregado, dano, sons de espada,
  `SCRPlayerFullPowerAndHp`). O cabelo/capa seguem o corpo (`nico_parentSync`).
* Cavalo: `gStatLoadHorseChara/Unload/Reload`, `gameflowChangeHorse`; o jogador Ã© "montado" por um vÃ­nculo de
  objetos (`nico_GetPlayerCharacterRidingHorseObject`).

## 7. Colossos (IA)

* Um colosso Ã© um personagem comum (kind 4) cujo script Ã© o laÃ§o genÃ©rico de chefe:
  `SCRExecBossPreProcess` (0x01377740) e `PostProcess` (0x013790E0) por quadro, e a IA em bytecode interpretado
  por `updateScriptDataSystem` (0x01453E48: percepÃ§Ã£o, raiva, olhos, arco/flecha...). A IA sÃ³ acorda quando o
  evento do arena roda `BootBoss`.
* Estado (`work = *GetSCRCharaAIWorkPtr(obj)`): +0x40 vida mÃ¡x., +0x44 vida atual, +0x54 nÂº de partes,
  +0x10 definiÃ§Ãµes das partes (0x58 bytes: +0 id, +0x1C mÃ¡x), +0xE8 bloco de dano (float por parte em
  `+0x24 + 8*i`), +0xEC flag "morte jÃ¡ tratada". `getBossPartsLifeRatio` = (mÃ¡x - dano)/mÃ¡x.
* Golpe: carta de dano -> `PreProcess` acha a parte pelo atributo de superfÃ­cie, soma dano, recalcula vida =
  mÃ¡x - dano. Vida <= 0 (cauda 0x013776C0): vida = 0, `LayoutClass+0x1AC = 1`, `playerEnableDeath = 0`
  (0x01465E88) e **sinaliza `DEAD_BOSS`**. O script de espera do arena (`st_boss_status`, 0x013D0C20) salta para
  `st_boss_dead` (0x013D0588): trava pausa, espera a animaÃ§Ã£o de morte, carrega o segmento do desfecho, cria
  os tentÃ¡culos (`CreateDeathTentacle`; poucos se a memÃ³ria livre < 1 MB), +4 HP mÃ¡x., e carrega o santuÃ¡rio.
  As animaÃ§Ãµes de morte sÃ£o carregadas sob demanda por `checkDeadMotionLoadByScriptDataSystem`.
* Atalhos que NÃƒO funcionam: `setBossAbsoluteDeath` (+0x48), sÃ³ encher o dano, ou o comando 0x01370000
  (marca +0xEC sem carregar a animaÃ§Ã£o -> "motion address is NULL: ..._death_front_loop.anb").

## 8. CÃ¢mera e UI

* CÃ¢mera por "cameraman" (`doit_cameraman`, `doit_game_camera`, 85 funÃ§Ãµes CAM*): regiÃµes de cÃ¢mera,
  cÃ¢mera de chefe (`gameflowBossEyeCameraDisable`), parÃ¢metros alocados a cada inÃ­cio de luta
  (`reset_game_camera_param_update_data_manager`, 0x65C bytes por entrada: foi a alocaÃ§Ã£o que falhou na luta).
* DiÃ¡logos/menus: janelas prÃ³prias (`createYesNoDialog`, `windowSetCursorPos`...), nÃ£o leem o pad pelo mesmo
  caminho dos botÃµes de jogo; confirmar = bit 0x40, voltar = 0x10 (medido); cursor nÃ£o foi achado.

## 9. O que isso significa para o co-op (ligaÃ§Ãµes)

* Clonar objetos = repetir `initstage` sobre os segmentos do jogador, com alocador zerando blocos; o jogo nÃ£o
  sabe dos clones, entÃ£o o `guard` apaga antes de `unloadstagecore` e restaura o cache da definiÃ§Ã£o.
* MemÃ³ria Ã© o gargalo (heap quase cheio): `heapgrow` doa RAM; toda checagem de ponteiro de objeto precisa de `in_heap`.
* Sincronizar colosso = pose (movimento+frame+raiz) + vida; matar = sequÃªncia do golpe final; progresso = flags
  de colosso mortos + Ã­ndice ativo + HP mÃ¡x.

## 11. Cutscenes (demo manager) — decompilado depois

* Uma cutscene é um **objeto-script `demo_common`** (0x0140DEF0) com partição de heap própria (`iosCreatePartition`,
  id 0x87) e um bloco de estado de 0x54 bytes apontado por `ScriptLayoutClass+0x2D8`. Layout do bloco: +0x00 estado
  (6 = carregada), +0x04 pedido de boot, +0x08 boot concluído, +0x0C "iniciar", +0x10 "terminar", +0x20/+0x2C
  ids de scripts de fim, +0x44 já começou, +0x48 id da cutscene, +0x4C/+0x50 progresso de carregamento.
* API (todas só mexem nesse bloco; o trabalho real é feito pelo script `demo_common` que fica em laço `scrRun`):
  `demoManBootRequest` (0x0140E330, liga o clipping próximo desligado e pede boot), `demoManBootChk`,
  `demoManStart(obj, id)` (0x0140E390: grava o id e zera flags), `demoManChkStart` (+0x44 != 0),
  `demoManChkLoadFinish`, `demoManEnd` (0x0140E400: cria scripts de fim, cancela legendas),
  `demoManSkip` (0x0140E4F0: pula a câmera via `SkipCutDemoCameraOrient`).
* `gameModeSet(mascara)` (0x01352B70) é **contado por referência**: o OR vai em `___gameMode` (0x01465D70) e cada bit
  incrementa um byte em 0x012DA4F0+bit; `gameModeCls` decrementa e só limpa o bit no zero. Bit 0 = cutscene/menu.
  Isso explica por que duas cutscenes aninhadas não soltam o modo cedo demais.
* Para o co-op: o ponto limpo para uma barreira de cutscene genérica é `demoManStart` (id em +0x48 do bloco), igual ao
  que `barrier.py` já faz em `bossStatStartDemo`; o fim é observável por `___gameMode & 1` voltar a 0.

## 12. IA dos colossos: é dirigida por dados, não bytecode

(Correção da seção 7: `updateScriptDataSystem` 0x01453E48 não interpreta bytecode; é um controlador por tipo,
alimentado por tabelas de funções com argumentos.)

* `work = GetSCRCharaAIWorkPtr(obj)`; `work+0x4090` = tipo do controlador: 0 pernas rápidas (`updateFastLegCtrlWork`),
  1 pernas lentas, 2 pássaro (`updateBirdCtrlWork`), 3 minhoca/serpente (`updateWormCtrlWork`), 4 lento+pássaro,
  6 outro, 7 cavalo. Estado em `work+0x3D10` (+0x3DC0 para a parte de pássaro). Alvo = jogador
  (`SCRGetFirstObj(0x01465940)`, guardado em `work+0x4078`). Por quadro também: `updatePerceptionWork` (visão/
  audição), `updateAngryWork` (raiva), `updateEyeWork`, `updateArrowThrustWork`, `updateHintWork`, `setSCRGazeStatus`.
* **Raiva** = float em `work+0x40F8`, limitado a 0..100: decai por quadro quando não está tomando dano e sobe ao
  levar golpe, conforme o tipo de dano (`DamageClass+0x60`: 0/5 usa parâmetro +8, 1/2/3/4 usa +4).
* **Tabelas de comportamento**: o estado atual é um registro (`work+0x409c`) que aponta parâmetros de movimento
  (`work+0x40A0`) copiados para os controladores (`setDashFastLegCtrlWork`, `setBackFastLegCtrlWork`,
  `setRunStopTurnFastLegCtrlWork`, `setAttainModePathWork`...) e listas de ações e condições:
  * **ação** (`execSCRBaseFunc` 0x01455FE8): lista de registros de 0x40 bytes `{função, arg...}` terminada em
    função nula; chama cada função com os argumentos do registro.
  * **condição** (`execCondition` 0x01455AE0): registros de 0x50 bytes `{modo, esperado, função, args...}`; modo 1 =
    todas devem retornar o esperado (E), modo 2 = qualquer uma (OU); encadeia até o próximo ponteiro nulo.
  As funções apontadas são os comandos `SCR*` (592 deles) e os dados vêm dos módulos (`SH_*`), por isso a IA de
  cada colosso é um conjunto de tabelas relocáveis, não código.

### Colossos que não aparecem ao teleportar (7, 10, 13, 14, 15) — resultado negativo

* Dump dos 16 registros (`+0x50` flags, `+0x51` área, `+0x1C` flag de boot, `+0x24` GObject, `+0x34/+0x38`
  segmentos, `+0xB0`, `+0xE8`): nenhum campo separa os 5 que faltam dos 11 que carregam (flags 0x4207/0x0F07/0x1607/
  0x1007/0x5502 vs 0x3707, 0x2306...). Para os faltantes `bossStatBootChk` (0x0134A458) devolve -1 (o GObject em
  `record+0xE8` não está registrado) e o heap livre fica ~800-950 mil palavras (quase nada carregou). Esperar 150 s
  com o jogador parado no ponto de chegada não muda nada (não é demora de streaming), e não existe nenhum
  personagem tipo 4 em nenhum lugar da RAM. Próxima hipótese: o GObject desses nasce por um gatilho de evento/
  posição fora do ponto de chegada (o #4 já aparecia a 641 m dele). Ainda não testado.

## 13. Carregamento de arquivos e GObjects

* **Fila de carga** (`sheetMgr*`, 0x012574F0-0x012594B0): um thread de I/O consome pedidos de 0x30 bytes
  (`[0]` tipo: 0 = carregar segmento, 1 = descarregar, 2 = dados de mapa; `+8` heap; `+0xC` segmento;
  `+0x28` ponteiro de status, preenchido com 0xDEAD enquanto pendente e 0xDEAF/0xDEB0 ao concluir). Quem pede
  (`stagemanager_loadstagecore`) espera em laço de `scrRun()` até o status mudar; 0xDEB0 = falhou/nada a instanciar.
* **Tipos de arquivo** (`loadCd*File`, 0x011F6B40-0x011FC238): XFF (módulo de código/dados), NMO (modelo), NSO,
  NTO (textura), NFB, movimento (`loadCdMotionFile`: arquivos de animação `anim/*.anb`), ColRes (colisão),
  WinTex. Todos seguem o mesmo padrão: `iosMallocInGarbagePtn(tamanho, 0x10)` numa partição "de lixo" ->
  `sheetFileRead` -> `isysCreateModuleObjExisted` (vira um objeto-módulo) -> `isysResolveOneModuleObj` (relocação por
  símbolo) -> adicionado a um grupo do isys. Se a alocação falha, o pedido chama
  `vschedSetNextFrameForceGarbage()` e espera (`sheetWait`) em laço até liberar memória: é o jeito do jogo
  sobreviver à falta de RAM em carga de assets (diferente das alocações de objeto, que travam).
* **GObject** (definição de objeto de jogo numa entrada de layout): `def+0x08` = id de flag de exibição,
  `def+0x0C` = endereço do objeto instanciado (`gStatGetGObjAdrs/SetGObjAdrs`; é o "cache" que os filhos
  procuram), `def+0x10` bit 0 = exibição ligada (`gStatGObjDispOn/Chk`), bit 1 = lógica invertida da flag
  (`gStatGobjDispGflagChk`: spawna se a flag estiver ligada, ou desligada quando o bit 1 está setado).
  `FUN_013600a8` só cria a entrada se `gStatGObjDispChk` = 1; `backStageGobjCheckEntry` filtra entradas de chefe
  (marcas 0x103/0x203/0x303): a entrada 0x303 só vale se for do colosso ATIVO. Por isso `bossStatSetActiveBoss`
  mexe nas flags: ativa só o GObject do colosso escolhido e desliga os demais.

## 14. HP, garra, dano e física (resumo; parte inferida pelos nomes, não decompilada a fundo)

* **HP** (`setPlayerHp` 0x01358298, `addPlayerHp` 0x01358318): float em 0x012DE264, travado em 0..máximo
  (fwork 0xB). Valores negativos passam pelo fator `gStatGetPlayerAccessoriesDamgeRaitoToPlayer` (dano recebido
  varia com acessórios). Toda mudança atualiza as barras de UI (4 slots de 0x10 bytes em 0x012F2930, razão
  HP/máx em 0x01466BB0..BB8, esmaecimento 0.1/quadro) e só anima se não houver cutscene/fade.
  `addPlayerMaxHp` (teto 570) toca o som de upgrade.
* **Dano ao jogador**: `SendDirectDamageLetter(valor, origem, alvo, geometria, tipo)` monta a carta; queda usa
  `nico_SendPlayerFallDownDamageMail` (valor em `classe+0x51C`, tipo -3). Dificuldade via `SetDamageClassDamageTableType`.
* **Física do personagem** (`charrootdyn_*` 84 funções, `motrootdyn_*` 31): colisão por "GP" (pontos de geometria):
  deslizamento (`slidecollision`), pouso (`setLandingStatus`, `getLandingSurface`), queda livre
  (`startfreefall`, `startmotionfall`), água (`checkUnderWater`, `getWaterStatus`), vento, bordas e paredes
  escaláveis (`detectCharClimbableWall`, `checkHangableEdgeWall`, `detectCharEdge`).
  **Pai geométrico**: `updateCharacterClassParentGeometry`, `getLocalOffsetFromParentGP`, `isParentToEdge`,
  `motrootdyn_parent_from_falldown`: um personagem pode ficar "preso" a um ponto de geometria de outro objeto e
  guardar a posição relativa a ele. É assim que o Wander acompanha o corpo em movimento do colosso ao escalar.
  Consequência para o co-op: o clone não agarra nem sobe no colosso (não tem pai geométrico); fazê-lo exigiria
  chamar essas rotinas (ainda não tentado).

## 15. Cutscenes de evento: onde começam (medido com probes)

* Durante a intro do colosso 0 e a morte dele, `createScrEventDemoManager` (0x0145AF70, 11 argumentos, chamado pelo
  script de evento em curso, cria um script-filho `scrDemoManager`) disparou 2 vezes na intro (duas sub-cenas) e
  0 na morte; `demoManStart` 0 vezes e `SCRFuncStartEventDemo` só é consultado por quadro (condição). Ou seja:
  `demoManStart` é das cenas de título/abertura; as cenas de evento usam `createScrEventDemoManager`; a morte do
  colosso (`st_boss_dead`) conduz a própria câmera sem passar por ele.
* `gameModeSet` roda centenas de vezes (menus, pausa, mapa): não serve de gancho para cutscene.
* Co-op: `barrier.DEMO` (detour em `createScrEventDemoManager`, preserva a0-a3/t0-t3) segura a cena por até
  `DEMO_BARRIER_SECONDS` (4 s) quando o par está a menos de `DEMO_NEAR` (150 m); libera quando o par também espera
  uma cena, já está em cena (flag `IN_DEMO` = bit 0 do modo de jogo) ou seu colosso já luta. A morte do colosso
  não passa por essa barreira (cada máquina toca a sua, disparadas pela vida sincronizada).

## 10. Lacunas (ainda nÃ£o decompilado a fundo)

* Os dados concretos das tabelas de IA de cada colosso (condições/ações por estado) e o motivo de 5 colossos não carregarem (seção 12).
* Sistema de cutscenes (`demoManStart`, `SDemo*`), Ã¡udio, memory card, fÃ­sica `charrootdyn`/`motrootdyn`.
* Formato interno dos arquivos (XFF/anb/lws/NMO) e do mapa do mundo (`SH_StageDataDEFAULT`); só o fluxo de carga está documentado (seção 13).
* Por que 5 colossos (7, 10, 13-15) nÃ£o aparecem ao teleportar (evento de spawn).
