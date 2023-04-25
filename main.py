#!/usr/bin/env python3
# coding: utf-8
import ffmpeg
import os
import re
import subprocess as sp
from subprocess import *
from optparse import OptionParser
from os import mkdir
from os.path import exists
from time import sleep

from multiprocess import Pool, Lock, Manager

orgDir      = "/home/localadmin/Audiobooks"
saveDir     = f"{orgDir}/converted"

def parseChapters(filename):
    chapters = []
    command = [ "ffmpeg", '-i', filename]
    output = ""
    try:
        output = sp.check_output(command, stderr=sp.STDOUT, universal_newlines=True)
    except CalledProcessError as e:
        output = e.output 
   
    lines   = output.splitlines()
    for line in lines:
        m       = re.match(r".*Chapter #(\d+:\d+): start (\d+\.\d+), end (\d+\.\d+).*", line)
        if m != None:
            cd,chp = m.group(1).split(":")
            chapter = { "cd": int(cd)+1, "chp":int(chp)+1, "start": m.group(2), "end": m.group(3)}
            chapters.append(chapter)
            
    return chapters

def getChapters( filename:str=None ):
    fdir    = os.path.dirname(filename)
    fname   = os.path.basename(filename)
    fbase, fext = os.path.splitext( fname )
    fext = fext[1:]

    chapters = parseChapters( filename )
    for chap in chapters:
        chap['bname']       = fbase
        chap["orgFile"]     = filename
    return chapters

def split_book(chapters,outBaseDir,stdoutlock):
    for c in chapters:
        outDir = f"{outBaseDir}/{c['bname']}"
        stdoutlock.acquire()
        if not exists(outDir):
            mkdir( outDir )
            print(f"Created {outDir}" )
        stdoutlock.release()
        
        cdnum = c['cd'] if c['cd']>=10 else f"0{c['cd']}"
        chnum = str(c['chp'])
        if c['chp'] < 10:
            chnum = f"00{c['chp']}"
        elif c['chp'] < 100:
            chnum = f"0{c['chp']}"


        outputfilename = f"{outDir}/{cdnum}-{chnum}-{c['bname']}.mp3"
        if exists( outputfilename ):
            stdoutlock.acquire()
            print( f"\tFile alreadyexists: {cdnum}-{chnum}-{c['bname']}.mp3" )
            stdoutlock.release()
            continue
        stdoutlock.acquire()
        print( f"\tCreating chapter: {cdnum}-{chnum}-{c['bname']}.mp3" )
        stdoutlock.release()
            
        command = ["ffmpeg", 
                    "-activation_bytes", "cbdab406",
                    "-vn", 
                    "-vsync", "2",
                    "-i", c["orgFile"],
                    "-ss", f'{c["start"]}',
                    "-to", f'{c["end"]}',
                    "-ar", "44100", 
                    "-b:a", "192k", 
                    "-ac", "2", 
                    "-f", "mp3",
                    outputfilename ]
        output = ""
        try:
            output = sp.check_output(command, stderr=sp.STDOUT, universal_newlines=True)
        except CalledProcessError as e:
            output = e.output 
        stdoutlock.acquire()
        print( f"\tCreated: {cdnum}-{chnum}-{c['bname']}.mp3" )
        stdoutlock.release()


audiobooks = {}   
with open("filelist.txt", encoding="utf-8") as filenamelist:
    for filename in filenamelist.readlines():
        filename = filename.strip()
        orgFileName = f"{orgDir}/{filename}"
        print( f"Parsing: {orgFileName}" )
        chapters = getChapters( orgFileName )
        audiobooks.update({filename: chapters})
        for c in chapters:
            cdnum = str(c['cd']) if c['cd']>=10 else f"0{c['cd']}"
            chnum = str(c['chp'])
            if c['chp'] < 10:
                chnum = f"00{c['chp']}"
            elif c['chp'] < 100:
                chnum = f"0{c['chp']}"


print()
with Manager() as manager:
    stdoutlock = manager.Lock()
    processes = []
    arg_map = []
    for fname,chapters in audiobooks.items():
        arg_map.append([chapters, saveDir, stdoutlock])

    with Pool() as p:
        print("Generating pool")
        result = p.starmap_async(split_book, arg_map)
        
        while not result.ready():
            print("Not finished")
            sleep(10)
