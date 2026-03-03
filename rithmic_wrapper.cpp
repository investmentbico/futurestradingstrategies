#include "RApiPlus.h"

#include <iostream>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <queue>

#define GOOD 0
#define BAD  1

using namespace std;
using namespace RApi;

class MyAdmCallbacks: public AdmCallbacks
{
public:
    MyAdmCallbacks() {};
    ~MyAdmCallbacks() {};

    virtual int Alert(AlertInfo * pInfo, void * pContext, int * aiCode);
    virtual int Environment(EnvironmentInfo * pInfo, void * pContext, int * aiCode);
    virtual int EnvironmentList(EnvironmentListInfo * pInfo, void * pContext, int * aiCode);
};

class MyCallbacks: public RCallbacks
{
public:
    MyCallbacks() {};
    ~MyCallbacks() {};

    virtual int Alert(AlertInfo * pInfo, void * pContext, int * aiCode);
    virtual int AskQuote(AskInfo * pInfo, void * pContext, int * aiCode);
    virtual int BidQuote(BidInfo * pInfo, void * pContext, int * aiCode);
    virtual int BestBidAskQuote(BidInfo * pBid, AskInfo * pAsk, void * pContext, int * aiCode);
    // Add other callbacks as needed
};

int MyAdmCallbacks::Alert(AlertInfo * pInfo, void * pContext, int * aiCode)
{
    *aiCode = API_OK;
    return (OK);
}

int MyAdmCallbacks::Environment(EnvironmentInfo * pInfo, void * pContext, int * aiCode)
{
    *aiCode = API_OK;
    return (OK);
}

int MyAdmCallbacks::EnvironmentList(EnvironmentListInfo * pInfo, void * pContext, int * aiCode)
{
    *aiCode = API_OK;
    return (OK);
}

int MyCallbacks::Alert(AlertInfo * pInfo, void * pContext, int * aiCode)
{
    *aiCode = API_OK;
    return (OK);
}

int MyCallbacks::AskQuote(AskInfo * pInfo, void * pContext, int * aiCode)
{
    cout << "ASK: " << string(pInfo->sTicker.pData, pInfo->sTicker.iDataLen) <<
" " << pInfo->dPrice << " " << pInfo->llSize << endl;
    *aiCode = API_OK;
    return (OK);
}

int MyCallbacks::BidQuote(BidInfo * pInfo, void * pContext, int * aiCode)
{
    cout << "BID: " << string(pInfo->sTicker.pData, pInfo->sTicker.iDataLen) <<
" " << pInfo->dPrice << " " << pInfo->llSize << endl;
    *aiCode = API_OK;
    return (OK);
}

int MyCallbacks::BestBidAskQuote(BidInfo * pBid, AskInfo * pAsk, void * pContext, int * aiCode)
{
    cout << "BEST: " << string(pBid->sTicker.pData, pBid->sTicker.iDataLen) <<
" BID:" << pBid->dPrice << " ASK:" << pAsk->dPrice << endl;
    *aiCode = API_OK;
    return (OK);
}

int main()
{
    REngine *        pEngine = NULL;
    MyAdmCallbacks * pAdmCallbacks = NULL;
    MyCallbacks *    pCallbacks = NULL;
    REngineParams    oParams;
    LoginParams      oLoginParams;
    int              iCode;

    // Create admin callbacks
    try
    {
        pAdmCallbacks = new MyAdmCallbacks();
    }
    catch (OmneException& oEx)
    {
        iCode = oEx.getErrorCode();
        cerr << "Error creating admin callbacks: " << iCode << endl;
        return BAD;
    }

    // Create regular callbacks
    try
    {
        pCallbacks = new MyCallbacks();
    }
    catch (OmneException& oEx)
    {
        iCode = oEx.getErrorCode();
        cerr << "Error creating callbacks: " << iCode << endl;
        delete pAdmCallbacks;
        return BAD;
    }

    // Set up engine parameters
    oParams.sAppName.pData = getenv("RITHMIC_APP_NAME");
    oParams.sAppName.iDataLen = oParams.sAppName.pData ? strlen(oParams.sAppName.pData) : 0;
    oParams.sAppVersion.pData = getenv("RITHMIC_APP_VERSION");
    oParams.sAppVersion.iDataLen = oParams.sAppVersion.pData ? strlen(oParams.sAppVersion.pData) : 0;
    oParams.pAdmCallbacks = pAdmCallbacks;
    oParams.sLogFilePath.pData = "rithmic_wrapper.log";
    oParams.sLogFilePath.iDataLen = strlen(oParams.sLogFilePath.pData);

    // Set up environment variables for Rithmic Test
    char * envp[] = {
        "MML_DMN_SRVR_ADDR=rituz00100.00.rithmic.com:65000~rituz00100.00.rithmic.net:65000~rituz00100.00.theomne.net:65000~rituz00100.00.theomne.com:65000",
        "MML_DOMAIN_NAME=rithmic_uat_dmz_domain",
        "MML_LIC_SRVR_ADDR=rituz00100.00.rithmic.com:56000~rituz00100.00.rithmic.net:56000~rituz00100.00.theomne.net:56000~rituz00100.00.theomne.com:56000",
        "MML_LOC_BROK_ADDR=rituz00100.00.rithmic.com:64100",
        "MML_LOGGER_ADDR=rituz00100.00.rithmic.com:45454~rituz00100.00.rithmic.net:45454~rituz00100.00.theomne.com:45454~rituz00100.00.theomne.net:45454",
        "MML_LOG_TYPE=log_net",
        "MML_SSL_CLNT_AUTH_FILE=rithmic_ssl_cert_auth_params",
        NULL
    };
    oParams.envp = envp;

    // Create engine
    try
    {
        pEngine = new REngine(&oParams);
    }
    catch (OmneException& oEx)
    {
        iCode = oEx.getErrorCode();
        cerr << "Error creating engine: " << iCode << endl;
        delete pAdmCallbacks;
        delete pCallbacks;
        return BAD;
    }

    // Set up login parameters
    oLoginParams.sMdUser.pData = getenv("RITHMIC_USER");
    oLoginParams.sMdUser.iDataLen = oLoginParams.sMdUser.pData ? strlen(oLoginParams.sMdUser.pData) : 0;
    oLoginParams.sMdPassword.pData = getenv("RITHMIC_PASSWORD");
    oLoginParams.sMdPassword.iDataLen = oLoginParams.sMdPassword.pData ? strlen(oLoginParams.sMdPassword.pData) : 0;
    oLoginParams.pCallbacks = pCallbacks;

    // Login
    iCode = pEngine->login(&oLoginParams, &iCode);
    if (iCode != API_OK)
    {
        cerr << "Login failed: " << iCode << endl;
        delete pEngine;
        delete pAdmCallbacks;
        delete pCallbacks;
        return BAD;
    }

    cout << "LOGGED_IN" << endl;

    // Read commands from stdin
    string line;
    while (getline(cin, line))
    {
        if (line == "quit")
            break;
        else if (line.find("quote ") == 0)
        {
            string ticker = line.substr(6);
            // Subscribe to market data
            tsNCharcb sExchange;
            tsNCharcb sTicker;

            sExchange.pData = (char*)"CME";
            sExchange.iDataLen = 3;
            sTicker.pData = (char*)ticker.c_str();
            sTicker.iDataLen = ticker.length();

            int iFlags = MD_BEST;  // Best bid/ask quotes
            pEngine->subscribe(&sExchange, &sTicker, iFlags, &iCode);
            if (iCode != API_OK)
            {
                cerr << "Subscribe failed: " << iCode << endl;
            }
        }
        // Add more commands
    }

    delete pEngine;
    delete pAdmCallbacks;
    delete pCallbacks;

    return GOOD;
}