; SPDX-License-Identifier: Apache-2.0
; Installer per Windows (Inno Setup 6). Si costruisce dopo PyInstaller:
;     iscc /DMirtoVersion=0.2.0 packaging\mirto.iss
; Installa per l'utente corrente, senza chiedere i diritti di amministratore: Mirto non ha
; servizi di sistema, e l'avvio all'accesso e' una voce del registro dell'utente.
; L'AppId non va mai cambiato: e' quello che lega una nuova versione all'installazione esistente
; (senza, la 0.3 si installerebbe accanto alla 0.2 invece che sopra).

#ifndef MirtoVersion
  #define MirtoVersion "0.0.0"
#endif

[Setup]
AppId={{725A6E59-1B0A-423B-9CED-5ADEB869AE55}
AppName=Mirto
AppVersion={#MirtoVersion}
AppVerName=Mirto {#MirtoVersion}
AppPublisher=Marco Dalicco
AppPublisherURL=https://github.com/marcopogo81/Mirto
DefaultDirName={autopf}\Mirto
DefaultGroupName=Mirto
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Mirto-{#MirtoVersion}-setup
SetupIconFile=..\mirto\gui\icons\app\app.ico
UninstallDisplayIcon={app}\Mirto.exe
UninstallDisplayName=Mirto {#MirtoVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 e' il minimo: PySide6 non gira sotto
MinVersion=10.0

[Languages]
Name: "italiano"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "avvioautomatico"; Description: "{cm:AvvioAutomatico}"; GroupDescription: "{cm:Sorveglianza}"

[CustomMessages]
italiano.AvvioAutomatico=Avvia Mirto quando accendo il computer
english.AvvioAutomatico=Start Mirto when I turn on the computer
italiano.Sorveglianza=Sorveglianza continua:
english.Sorveglianza=Continuous surveillance:
italiano.ApriDopo=Avvia Mirto
english.ApriDopo=Launch Mirto

[Files]
Source: "..\dist\Mirto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Mirto"; Filename: "{app}\Mirto.exe"
Name: "{group}\{cm:UninstallProgram,Mirto}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Mirto"; Filename: "{app}\Mirto.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "Mirto"; ValueData: """{app}\Mirto.exe"""; Flags: uninsdeletevalue; Tasks: avvioautomatico
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; \
    ValueName: "Mirto"; Flags: deletevalue uninsdeletevalue; Tasks: not avvioautomatico

[Run]
Filename: "{app}\Mirto.exe"; Description: "{cm:ApriDopo}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; La cartella dei dati (configurazione, eventi, registrazioni) NON si tocca: e' roba dell'utente
; e sta in %LOCALAPPDATA%\Mirto. Qui si cancella solo quello che ha creato l'installer.
Type: dirifempty; Name: "{app}"
