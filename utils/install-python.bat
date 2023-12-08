@echo off
setlocal enabledelayedexpansion

:: Define the Python installer URL and installation directory
set "pythonInstallerURL=https://www.python.org/ftp/python/3.11.5/python-3.11.5.exe"
set "installDir=C:\Python"  :: Replace with your desired installation directory

:: Check if Python is already installed
python --version >nul 2>&1
if %errorlevel%==0 (
  echo Python is already installed. Skipping installation.
  goto :end
)

:: Get my_user_path
for /f "usebackq tokens=2,*" %%A in (`reg query HKCU\Environment /v PATH`) do set my_user_path=%%B

:: Download the latest Python installer
echo Downloading the latest Python installer...
powershell -command "(New-Object System.Net.WebClient).DownloadFile('%pythonInstallerURL%', 'python-installer.exe')"

:: Install Python
echo Installing Python...
python-installer.exe /quiet TargetDir=%installDir%

:: Check if Python installation was successful
if exist "%installDir%\python.exe" (
  echo Python has been installed successfully.
  
  :: Add Python to the user PATH  
  echo my user path %my_user_path%
  setx PATH "%installDir%;%installDir%\Scripts;%my_user_path%"
  echo Added python installation to Path.
) else (
  echo Failed to install Python.
  exit /b 1
)

:: Clean up: Delete the installer
timeout /t 1 /nobreak
del python-installer.exe

:end
:: End of script
