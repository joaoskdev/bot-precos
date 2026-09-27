# Bot de preços no Telegram

Você manda o link de um anúncio (Mercado Livre, KaBuM, Pichau, Terabyte e outras lojas) e o bot responde com o preço atual. Depois disso ele confere o preço **a cada 1 hora** e te avisa quando mudar.

Ele roda de graça no **GitHub Actions**, então não precisa de servidor nem do seu PC ligado.

## Como funciona

O GitHub executa o bot a cada ~10 minutos. Em cada execução ele:

1. lê as mensagens que você mandou no Telegram e responde;
2. confere o preço dos produtos que não foram checados na última hora;
3. te avisa se algum preço mudou (ou se o produto esgotou ou voltou ao estoque);
4. salva a lista de produtos em `dados/estado.bin` (criptografado) dentro do próprio repositório.

Por isso **as respostas não são instantâneas**: quando você manda um link, o preço chega em uns 10 minutos. Em horários de pico o GitHub às vezes atrasa um pouco mais.

## Comandos

| Comando | O que faz |
|---|---|
| *(mandar um link)* | cadastra o anúncio e responde com o preço |
| `/lista` | produtos monitorados, preço atual e menor preço visto |
| `/verificar` | checa todos os preços agora, sem esperar a hora |
| `/historico 3` | histórico de preço do produto nº 3 |
| `/remover 3` | para de monitorar o produto nº 3 |
| `/ajuda` | ajuda |

---

## Passo a passo para colocar no ar

### 1. Criar o bot no Telegram

1. No Telegram, abra uma conversa com **@BotFather**.
2. Mande `/newbot`, escolha um nome e um usuário (tem que terminar em `bot`, ex: `joao_precos_bot`).
3. Ele te devolve um **token** parecido com `123456789:AAH...`. Guarde, é a senha do bot.

### 2. Criar o repositório no GitHub

1. Crie uma conta em <https://github.com> (se ainda não tiver).
2. Clique em **New repository**, dê um nome (ex: `bot-precos`) e marque **Public**.
   - Precisa ser **público** porque aí o GitHub Actions é ilimitado. Em repositório privado o plano grátis dá 2.000 minutos por mês, e o bot gastaria uns 4.300.
   - Sua lista de produtos fica **criptografada**, então ninguém consegue ler. O token fica nos *Secrets*, que também não aparecem para ninguém.
3. Suba os arquivos deste projeto:
   - Pelo site: **Add file → Upload files** e arraste **todo o conteúdo** da pasta, inclusive a pasta `.github`. Depois clique em **Commit changes**.
   - Ou pelo terminal:
     ```bash
     git init && git add . && git commit -m "bot de preços"
     git branch -M main
     git remote add origin https://github.com/SEU_USUARIO/bot-precos.git
     git push -u origin main
     ```
   - Confira se o arquivo `.github/workflows/bot.yml` apareceu no repositório. Sem ele o bot não roda.

### 3. Colocar o token no GitHub

1. No repositório: **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `TELEGRAM_TOKEN`. Secret: o token do BotFather. Clique em **Add secret**.

### 4. Ativar

1. No Telegram, abra o seu bot e mande **/start**.
2. No GitHub, vá na aba **Actions**. Se ele pedir, clique para habilitar os workflows.
3. Clique em **bot-precos → Run workflow → Run workflow** para rodar a primeira vez na hora.
4. Em ~1 minuto o bot te responde dizendo que agora ele é seu. **Só o primeiro chat que manda /start consegue usar o bot**; ele ignora mensagens de outras pessoas.
5. Pronto, é só mandar links. Daqui pra frente ele roda sozinho a cada ~10 minutos.

---

## Testar um link no seu PC (opcional, mas recomendado)

Antes de subir, dá pra ver se o bot consegue ler o preço de uma loja:

```bash
pip install -r requirements.txt
python testar.py "https://www.kabum.com.br/produto/..." "https://www.pichau.com.br/..."
```

Ele mostra nome, preço, disponibilidade e qual método achou o preço.

## Limitações

- **Algumas lojas bloqueiam robôs.** O bot se passa por um navegador real, mas os servidores do GitHub ficam em datacenters, e lojas como o Mercado Livre às vezes pedem captcha para esses endereços. Um link pode funcionar no `testar.py` do seu PC e falhar no GitHub. Se uma loja falhar 3 vezes seguidas, o bot te avisa e continua tentando.
- **O preço lido é o "à vista"** que a loja publica para o Google (geralmente o preço no PIX). Em anúncios com variações (cor, tamanho), vale o menor preço entre elas.
- O bot faz um commit por hora no repositório para salvar o estado. Isso é normal.

## Se parar de funcionar

- **Aba Actions com X vermelho:** clique na execução e veja o erro. O mais comum é o token errado no secret.
- **Trocou o token do bot no BotFather?** A lista é criptografada com o token antigo. Antes de trocar o secret `TELEGRAM_TOKEN`, crie um secret `STATE_KEY` com o **token antigo** como valor. Se não fizer isso, apague `dados/estado.bin` e cadastre os links de novo.
- **Uma loja mudou o site e parou de ler:** rode `python testar.py LINK` e veja o que aparece. A extração fica toda em `lojas.py`.

## Arquivos

| Arquivo | Para quê |
|---|---|
| `main.py` | lógica do bot (um ciclo por execução) |
| `lojas.py` | baixa a página e descobre o preço |
| `telegram_api.py` | conversa com o Telegram |
| `estado.py` | salva/carrega a lista criptografada |
| `testar.py` | teste manual de links |
| `.github/workflows/bot.yml` | agenda a execução no GitHub Actions |
