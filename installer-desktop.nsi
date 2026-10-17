; NIQ Desktop - NSIS Installer
!define APP_NAME "NIQ Desktop"
!define APP_VERSION "3.0.0"
!define APP_PUBLISHER "A'im Done Ekibi"
!define APP_URL "https://github.com/cenkergultekin/niq-project"
!define APP_EXE "NIQ-Desktop.exe"
!define INSTALL_DIR "$PROGRAMFILES64\${APP_NAME}"
!define UNINST_NAME "Uninstall.exe"

!include "MUI2.nsh"
!include "FileFunc.nsh"

Name "${APP_NAME}"
OutFile "NIQDesktop_Setup.exe"
InstallDir "${INSTALL_DIR}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
!define MUI_ICON "frontend\static\images\niq.ico"
!define MUI_UNICON "frontend\static\images\niq.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Turkish"

Section "${APP_NAME}" SecMain
  SectionIn RO
  SetOutPath "${INSTALL_DIR}"

  ; Zorunlu: dist\NIQ-Desktop.exe bu script ile aynı repo klasöründeki dist altında olmalı
  File "dist\${APP_EXE}"
  File "dist\NIQ-MCP.exe"
  File /nonfatal "LICENSE.txt"

  ; Masaüstü kısayolu
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "${INSTALL_DIR}\${APP_EXE}" "" "${INSTALL_DIR}\${APP_EXE}" 0

  ; Başlat menüsü kısayolu
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "${INSTALL_DIR}\${APP_EXE}" "" "${INSTALL_DIR}\${APP_EXE}" 0
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Kaldır.lnk" "$INSTDIR\${UNINST_NAME}"

  ; Registry – Programs & Features
  WriteRegStr HKLM "Software\${APP_NAME}" "InstallDir" "${INSTALL_DIR}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "URLInfoAbout" "${APP_URL}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "InstallLocation" "${INSTALL_DIR}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayIcon" "${INSTALL_DIR}\${APP_EXE}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" "$\"${INSTALL_DIR}\${UNINST_NAME}$\""
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoRepair" 1

  ; Uninstaller
  WriteUninstaller "${INSTALL_DIR}\${UNINST_NAME}"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Kaldır.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"

  Delete "${INSTALL_DIR}\${APP_EXE}"
  Delete "${INSTALL_DIR}\NIQ-MCP.exe"
  Delete "${INSTALL_DIR}\${UNINST_NAME}"
  Delete "${INSTALL_DIR}\LICENSE.txt"
  RMDir  "${INSTALL_DIR}"

  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
  DeleteRegKey HKLM "Software\${APP_NAME}"
SectionEnd


