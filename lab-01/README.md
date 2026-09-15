# Лабораторна робота 1 — середовище та професійний Git workflow

## Мета й результат

Мета роботи — отримати відтворюване робоче середовище та захищений публічний репозиторій, у якому зміни до `main` проходять через pull request. Налаштування GitHub збережено як код у [`config/repository-settings.json`](../config/repository-settings.json), а застосовується воно скриптом [`scripts/setup-repository.ps1`](../scripts/setup-repository.ps1).

Репозиторій: <https://github.com/StanislavHst/devops-portfolio>

## 1. Git і редактор

### Глобальна конфігурація Git

Налаштування виконано з PowerShell:

```powershell
git config --global user.name "Stanislav Rotar"
git config --global user.email "rotarstanislav4@gmail.com"
git config --global init.defaultBranch main
git config --global core.autocrlf true
git config --global pull.rebase true
git config --global core.editor "code --wait"
git config --list --global
```

Фактичний вивід перевірки:

```text
user.name=Stanislav Rotar
user.email=rotarstanislav4@gmail.com
init.defaultbranch=main
core.autocrlf=true
core.editor=code --wait
pull.rebase=true
```

Пошта відповідає GitHub-акаунту; у налаштуваннях GitHub підтверджено наявність verified email. `init.defaultBranch=main` уніфікує назву першої гілки нових репозиторіїв. `core.editor=code --wait` відкриває VS Code для повідомлень комітів і змушує Git дочекатися закриття вкладки.

#### Чому `core.autocrlf` залежить від ОС

Windows традиційно використовує `CRLF` (`\r\n`), а Unix/Linux/macOS — `LF` (`\n`). На Windows значення `true` перетворює `LF` із репозиторію на `CRLF` у робочій копії та повертає `LF` під час коміту. На Unix зазвичай використовують `input`: локальні `LF` не змінюються, а випадкові `CRLF` нормалізуються до `LF` під час коміту.

Якщо це не узгодити в команді зі змішаними ОС, Git може показати весь файл зміненим лише через кінці рядків. Наслідки: шумні diff, зайві конфлікти, нестабільні formatter/linter та shell-скрипти з помилкою `bad interpreter: /bin/bash^M`. Репозиторій додатково фіксує `end_of_line = lf` у `.editorconfig`.

#### Що змінює `pull.rebase=true`

Типовий `git pull` може створити merge commit, якщо локальна й віддалена історії розійшлися. З `pull.rebase=true` локальні коміти тимчасово знімаються, віддалені коміти застосовуються першими, а локальні перевідтворюються поверх них. Історія стає лінійною та легше читається. Недолік: rebase переписує SHA локальних комітів, тому не можна без узгодження rebase-ити вже опубліковані спільні коміти.

### VS Code і розширення

VS Code та розширення встановлено через консоль:

```powershell
winget install --id Microsoft.VisualStudioCode --exact
code --install-extension eamodio.gitlens
code --install-extension ms-azuretools.vscode-docker
code --install-extension redhat.vscode-yaml
code --install-extension yzhang.markdown-all-in-one
code --install-extension editorconfig.editorconfig
```

Призначення:

| Розширення | Навіщо |
| --- | --- |
| GitLens | історія змін, blame й авторство рядків у редакторі |
| Docker | Dockerfile, Compose, контейнери та валідація |
| YAML (Red Hat) | синтаксис і перевірка YAML за JSON Schema, зокрема GitHub Actions |
| Markdown All in One | preview і зручне редагування Markdown |
| EditorConfig | реальне застосування правил `.editorconfig` у VS Code |

У [`.vscode/settings.json`](../.vscode/settings.json) увімкнено `editor.formatOnSave`, YAML validation і схеми для GitHub Actions та Compose. Файл [`.editorconfig`](../.editorconfig) визначає UTF-8, пробіли, LF, фінальний перенос і видалення зайвих пробілів.

Скріншот навмисно некоректного GitHub Actions YAML: [yaml-schema-error.png](assets/yaml-schema-error.png). Помилковий YAML після перевірки видалено, щоб не залишати невалідну конфігурацію в робочій гілці.

## 2. Середовища виконання

Інструменти встановлено через консоль:

```powershell
winget install --id Schniz.fnm --exact
winget install --id astral-sh.uv --exact

fnm install --lts
fnm install 22
uv python install 3.14
```

Фактичні версії:

```text
fnm 1.39.0
Node.js v24.21.0 (LTS)
Node.js v22.23.2
uv 0.12.15
Python 3.14.7
Docker version 26.0.0, build 2ae903e
Docker Compose version v2.26.1-desktop.1
```

Перемикання між двома Node.js без перевстановлення системи:

```powershell
fnm exec --using 22.23.2 node --version
# v22.23.2

fnm exec --using 24.21.0 node --version
# v24.21.0
```

Файл `.node-version` фіксує Node `24.21.0`, а `.python-version` — Python `3.14`, тому новий учасник команди може відтворити потрібне середовище. Менеджер версій потрібен, коли різні проєкти залежать від різних runtime: він прибирає конфлікти глобальної інсталяції, дозволяє швидко перемикатися та робить локальне середовище ближчим до CI/production.

Команда `docker compose version` використовує Compose v2 як plugin Docker CLI. Команда зі старим дефісом (`docker-compose`) не використовується.

## 3. GitHub і доступ

- профіль: ім'я й фото заповнені;
- verified email: підтверджено в GitHub Settings;
- 2FA: **Enabled**, основний метод — authenticator app;
- SSH: використовується ключ типу Ed25519 із парольної фразою;
- Git remote після перевірки SSH переводиться на `git@github.com:StanislavHst/devops-portfolio.git`.

Перевірка SSH:

```powershell
ssh -T git@github.com
# Hi StanislavHst! You've successfully authenticated, but GitHub does not provide shell access.
```

### Чому приватний ключ не можна комітити

Публічний ключ — це ідентифікатор, який дозволено передавати GitHub. Приватний ключ доводить особу власника. Якщо він потрапляє в репозиторій, будь-хто з копією може діяти від імені власника в усіх системах, де цей ключ авторизовано; простого видалення останнім комітом недостатньо, бо секрет залишається в історії, fork, clone та кешах.

Послідовність реагування на витік:

1. Вважати ключ скомпрометованим одразу, не чекати доказів використання.
2. Видалити/revoke публічний ключ у GitHub та інших сервісах, де він був доданий.
3. Згенерувати **нову** пару ключів із новою парольної фразою; старий ключ повторно не використовувати.
4. Додати новий public key лише в потрібні сервіси й перевірити доступ.
5. Перевірити Security log, audit log, коміти, deploy keys і активні сесії на підозрілу активність; за потреби відкликати токени та сесії.
6. Видалити секрет з усієї Git-історії (`git filter-repo` або BFG), узгодити force-push очищеної історії з командою та попросити всіх зробити fresh clone.
7. Додати запобіжники: `.gitignore`, secret scanning/pre-commit hooks, мінімальні права та ротацію ключів.

Очищення історії зменшує поширення секрету, але **не повертає довіру** до ключа; revoke і ротація завжди виконуються першими.

## 4. Портфоліо-репозиторій

Створено публічний `StanislavHst/devops-portfolio`. Головний README містить представлення, DevOps-мету, поточні та заплановані технології, річний roadmap і контакти.

`.gitignore` не писався вручну. Він згенерований сервісом gitignore.io/Toptal для шаблонів `Node`, `Python`, `VisualStudioCode`, `Windows`:

```powershell
Invoke-WebRequest `
  'https://www.toptal.com/developers/gitignore/api/node,python,visualstudiocode,windows' `
  -OutFile .gitignore
```

Обрано MIT License: для навчального портфоліо доречна коротка permissive-ліцензія, яка дозволяє повторне використання прикладів за умови збереження copyright notice й не накладає складних copyleft-вимог.

Створено каталоги для `lab-01`…`lab-10` і `final`; порожні майбутні каталоги містять README-заглушки, бо Git не зберігає порожні директорії.

## 5. Правила репозиторію

Ruleset `Protect main` застосовується до default branch і містить:

- pull request обов'язковий перед merge;
- force-push заборонено правилом `non_fast_forward`;
- видалення `main` заборонено;
- історія має бути лінійною;
- дозволено squash/rebase merge, merge commits вимкнено;
- гілка після merge видаляється автоматично.

Поки не ввімкнено required status checks: GitHub може запропонувати check лише після першого запуску відповідного job, а CI з'явиться в Lab 04. Також не вимагається approval іншої людини: автор PR не може схвалити власний PR, тому в solo-репозиторії це створило б deadlock.

### Що зміниться в команді

Коли з'явиться команда, буде встановлено щонайменше один approval, dismiss stale approvals після нового push, вимогу вирішити review threads, Code Owners для критичних директорій, required CI/security checks і заборону bypass для адміністраторів. У команді незалежний reviewer або інший адміністратор може допомогти, тому заборона bypass захищає процес від одноосібного обходу. У solo-репозиторії та сама заборона разом із required approval заблокує єдиного власника без законного шляху merge чи аварійного відновлення.

### Project і шаблон PR

Project `DevOps Portfolio Roadmap` має колонки `Backlog`, `In Progress`, `Review`, `Done`, окрему issue для кожної лабораторної та фінального проєкту. Вбудовані workflows переводять нову задачу в Backlog, відкритий PR у Review, merged PR і closed issue у Done.

Шаблон `.github/pull_request_template.md` містить опис, посилання `Closes #`, self-check, точні команди перевірки, ризики та rollback.

## Бонус: правила як код

Конфігурація GitHub не залежить від пам'яті людини або набору кліків:

- `config/repository-settings.json` — декларативні параметри репозиторію, ruleset, Project і перелік задач;
- `scripts/setup-repository.ps1` — ідемпотентний скрипт, який створює/оновлює репозиторій через `gh` і GitHub API.

Застосування до нового порожнього репозиторію однією командою:

```powershell
.\scripts\setup-repository.ps1 `
  -Owner StanislavHst `
  -Repository devops-portfolio-demo
```

Кліки погано масштабуються: їх не видно в code review, неможливо точно повторити, легко пропустити параметр, важко порівняти середовища й відновити стан після помилки. Configuration as Code дає version control, diff, review, повторюваність, автоматичне застосування та аудит — ту саму базову ідею, що використовується в CI/CD і Infrastructure as Code.

## Git workflow цієї роботи

```text
main (bootstrap README)
  └── lab-01/setup (усі зміни лабораторної)
        └── pull request → squash merge → main
```

Прямого push із роботою в `main` немає. Початковий README створив GitHub під час створення порожнього репозиторію; вся лабораторна виконана у feature branch і зливається pull request.

## Команди фінальної перевірки

```powershell
git status --short --branch
git log --oneline --graph --decorate --all
git config --list --global
fnm list
fnm exec --using 22.23.2 node --version
fnm exec --using 24.21.0 node --version
uv run --python 3.14 python --version
docker --version
docker compose version
ssh -T git@github.com
gh api repos/StanislavHst/devops-portfolio/rulesets
gh project list --owner StanislavHst
```

## План усного захисту на чистому репозиторії

Нижче — сценарій, який можна виконати послідовно за 10–15 хвилин. Перед захистом варто відкрити PowerShell, GitHub-репозиторій, Project і цей звіт в окремих вкладках.

### 1. Показати, що середовище справді кероване

```powershell
git --version
git config --list --global
fnm --version
fnm list
fnm exec --using 22.23.2 node --version
fnm exec --using 24.21.0 node --version
uv --version
uv run --python 3.14 python --version
docker --version
docker compose version
```

Що сказати: «Node і Python не прив’язані до однієї глобальної версії. `fnm` та `uv` дозволяють відтворити runtime конкретного проєкту. Файли `.node-version` і `.python-version` зберігають вибір у Git. Compose перевіряю командою з пробілом — це v2 plugin, а не застарілий окремий `docker-compose`».

При перемиканні Node звернути увагу викладача на різні рядки `v22.23.2` і `v24.21.0`. Це прямий доказ, а не скріншот встановленого пакета.

### 2. Пояснити Git-конфігурацію

У виводі `git config --list --global` послідовно показати:

- `user.name` / `user.email` — авторство та прив’язка contribution до verified GitHub email;
- `init.defaultbranch=main` — стандартна назва першої гілки;
- `core.autocrlf=true` — Windows checkout у CRLF, нормалізація коміту в LF;
- `pull.rebase=true` — локальні коміти перевідтворюються над remote, без випадкових merge commits;
- `core.editor=code --wait` — Git чекає завершення редагування повідомлення у VS Code.

Важлива фраза: «`pull.rebase=true` не означає, що можна переписувати спільну історію. Я rebase-ю лише власні локальні коміти; `main` додатково захищений від force-push».

### 3. Показати EditorConfig і schema validation

Відкрити `.editorconfig`, `.vscode/settings.json` та `lab-01/assets/yaml-schema-error.png`.

Що сказати: «`.editorconfig` — спільний контракт форматування незалежно від особистих налаштувань IDE. Окреме розширення EditorConfig потрібне, бо VS Code сам файл не застосовує. Red Hat YAML отримує GitHub Workflow JSON Schema зі settings і знаходить не лише синтаксичні, а й структурні помилки. На скріншоті YAML синтаксично читається, але поле `jobs` має неправильний тип — очікується object».

### 4. Показати GitHub security

```powershell
ssh-keygen -lf $env:USERPROFILE\.ssh\id_ed25519.pub
ssh -T git@github.com
git remote -v
```

Відкрити GitHub **Settings → Password and authentication** і показати `Two-factor authentication: Enabled`, але не відкривати recovery codes. Потім **Settings → SSH and GPG keys** і показати доданий Ed25519 key.

Що сказати: «Парольна фраза захищає приватний ключ у стані спокою. GitHub отримує лише `.pub`. Навіть якщо історію очистити після витоку приватного ключа, довіра не відновлюється — спочатку revoke, потім нова пара, аудит, і лише після цього cleanup історії».

### 5. Створити тестовий репозиторій однією командою

Назву краще зробити унікальною, щоб скрипт не зіткнувся зі старою демонстрацією:

```powershell
$demoRepository = "devops-portfolio-demo-$(Get-Date -Format yyyyMMdd-HHmm)"
.\scripts\setup-repository.ps1 `
  -Owner StanislavHst `
  -Repository $demoRepository
```

Очікувано скрипт покаже шість етапів і завершиться `Repository configuration applied successfully`. Пояснити, що команда:

1. створює публічний репозиторій з bootstrap README;
2. застосовує merge/repository settings;
3. створює або оновлює ruleset для default branch;
4. створює labels і 11 issues;
5. створює та прив’язує Project із чотирма workflow-станами;
6. додає задачі в Backlog.

Скрипт ідемпотентний: повторний запуск оновлює ruleset і повторно використовує issues/Project замість створення дублікатів.

### 6. Перевірити результат тільки через CLI

```powershell
gh repo view "StanislavHst/$demoRepository" `
  --json visibility,mergeCommitAllowed,rebaseMergeAllowed,squashMergeAllowed,deleteBranchOnMerge

gh api "repos/StanislavHst/$demoRepository/rulesets" `
  --jq '.[] | {name,enforcement,target}'

gh issue list --repo "StanislavHst/$demoRepository" --limit 20
gh project list --owner StanislavHst
```

Потім у браузері швидко показати Settings → Rules → Rulesets і Project board. Це візуальна перевірка результату, але джерелом налаштувань залишаються versioned JSON і script.

### 7. Показати правильний PR workflow

```powershell
git log --oneline --graph --decorate --all
gh pr view 12 --repo StanislavHst/devops-portfolio --web
```

Що сказати: «Початковий README створив GitHub як bootstrap порожнього репозиторію. Після цього я відразу створив `lab-01/setup`. Усі файли лабораторної потрапляють у `main` тільки через PR #12. Squash merge залишає один змістовний коміт і підтримує лінійну історію».

Показати, що PR description має `Closes #1`, self-check і команди перевірки. Після merge issue #1 автоматично переходить у closed/Done, а feature branch видаляється.

### 8. Безпечно прибрати demo-репозиторій

Тільки після перевірки повної назви:

```powershell
Write-Host "Deleting only: StanislavHst/$demoRepository"
gh repo delete "StanislavHst/$demoRepository" --yes
```

Не використовувати glob або статичну назву основного репозиторію. Видаляється лише одноразовий demo; `devops-portfolio` залишається.

## Короткі відповіді на типові питання викладача

**Чому public repository?** На безкоштовному personal plan потрібні правила для публічного репозиторію; крім того, це портфоліо для зовнішнього перегляду.

**Чому MIT, а не GPL?** MIT мінімально обмежує повторне використання навчальних прикладів. GPL було б доречніше, якби метою було зобов’язати похідні роботи залишатися open source.

**Чому не ввімкнені status checks?** До першого запуску workflow GitHub не знає назви job/check. Реальний required check додається після CI у Lab 04.

**Чому zero required approvals?** У personal solo workflow автор не може схвалити власний PR. Процес PR уже дає явний diff і контрольовану точку merge; незалежний approval з’явиться разом із командою.

**Чому дозволені squash і rebase, але не merge commit?** Обидва дозволені методи сумісні з linear history. Для лабораторних використовую squash: проміжні технічні коміти PR стають одним зрозумілим комітом у `main`.

**Навіщо правила як код, якщо їх можна наклікати?** Код можна review-ити, порівнювати, повторно запускати, застосовувати до десятків репозиторіїв і використовувати для disaster recovery. Кліки не дають надійного diff чи відтворюваності.

**Що буде неправильним у поточному рішенні для команди?** Zero approvals і можливість admin bypass. Для команди слід додати reviewer/Code Owners, required CI/security checks, stale review dismissal, thread resolution і заборону bypass.
