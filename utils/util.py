from collections import defaultdict
import chess
import random
import numpy
from reconchess.utilities import *
from utils.scoring_utils import *
import utils.parallelism_utils as parallel
import time

from utils.exceptions import EmptyBoardDist

def product(list):
    return numpy.prod(list)

def normalize(dist, adjust = False, giveToZeros=.10, raiseNum = 0):
    if len(dist) == 0:
        raise(EmptyBoardDist)
    total = sum(dist.values())
    if adjust and total == 0:
        total = len(dist)
        for e in dist:
            dist[e] = 1/total
    else:
        if raiseNum > 0:
            for e in dist:
                dist[e] = dist[e] ** 2
            total = sum(dist.values())
            for e in dist:
                dist[e] /= total
            return normalize(dist, adjust=adjust, giveToZeros=giveToZeros, raiseNum=raiseNum-1)
        if adjust and giveToZeros > 0 and len(dist)>1:
            assert giveToZeros < 1
            if giveToZeros:
                zeroThreshold = giveToZeros/(len(dist)-1)
                numZeros = sum([1 for x in dist.values() if x <= zeroThreshold])
                total = sum([x for x in dist.values() if x > zeroThreshold])
                if numZeros == len(dist):
                    for e in dist:
                        dist[e] = 1/len(dist)
                    return dist
                elif numZeros/len(dist) < giveToZeros:
                    total = sum(dist.values())
                    for e in dist:
                        dist[e] /= total
                    return dist
                for e in dist:
                    if dist[e] <= zeroThreshold:
                        dist[e] = (1/numZeros) * giveToZeros
                    else:
                        dist[e] = (dist[e]/total) * (1-giveToZeros)
        else:
            for e in dist:
                dist[e] /= total
    return dist

def normalize_board_dist_helper(fen, dist):
    board = chess.Board(fen)
    boardScore = score(board, .15, not board.turn)
    dist[fen] = boardScore
##SHOULD ONLY BE CALLED BY US
def normalize_our_board_dist(dist, ourColor):
    if len(dist) == 0:
        raise(EmptyBoardDist)
    if len(dist) == 1:
        return normalize(dist, adjust=True)
    mostLikelyBoard = list(sorted(dist, key=dist.get, reverse=True))[0]
    mostLikelyBoards = list(sorted(dist, key=dist.get, reverse=True))[:20]
    mostLikelyValues = [dist[x] for x in mostLikelyBoards]
    likelihood = dist[mostLikelyBoard]
    total = sum(dist.values())
    for fen in dist:
        board = chess.Board(fen)
        if len(board.attackers(not ourColor, board.king(ourColor)))>0:
            dist[fen] = max(likelihood, total/min(3, len(dist)))
    #If all boards have the same value.
    if 100 > len(dist) > 1 and all(x >= likelihood/2-.0001 for x in mostLikelyValues):
        print(f"adjusting dist of size {len(dist)}...")
        t0 = time.time()
        parallel.run_parallel(normalize_board_dist_helper, list((fen, dist) for fen in list(dist.keys())))
        dist = normalize(dist, adjust=True, giveToZeros=.3, raiseNum=6)
        print(f"Completed after {time.time()-t0} seconds")
    return normalize(dist, adjust=True)

def sample(dist, k=1):
    if k==1:
        return random.choices(list(dist.keys()), weights=list(dist.values()), k=1)[0]
    else:
        return random.choices(list(dist.keys()), weights=list(dist.values()), k=k)

# Produce a sense result from a hypothetical true board and a sense square
def simulate_sense(board, square):  # copied (with modifications) from LocalGame
    if square is None:
        # don't sense anything
        senseResult = []
    else:
        if square not in list(chess.SQUARES):
            raise ValueError('LocalGame::sense({}): {} is not a valid square.'.format(square, square))
        senseResult = []
        senseSquares = get_sense_squares(square)
        for senseSquare in senseSquares:
            senseResult.append((senseSquare, board.piece_at(senseSquare)))
    return tuple(senseResult)

def get_sense_squares(square):
    rank, file = chess.square_rank(square), chess.square_file(square)
    senseSquares = set()
    for delta_rank in [1, 0, -1]:
            for delta_file in [-1, 0, 1]:
                if 0 <= rank + delta_rank <= 7 and 0 <= file + delta_file <= 7:
                    senseSquares.add(chess.square(file + delta_file, rank + delta_rank))
    return senseSquares

#Gets all moves that are reconchess-legal on a chess board
def get_all_moves(board : chess.Board):
    return move_actions(board) + [chess.Move.null()]

def king_capture_moves(board : chess.Board):
    return {move for move in board.pseudo_legal_moves if real_capture_square_of_move(board, move) == board.king(not board.turn)}

def into_check_moves(board : chess.Board):
    intoCheckMoves = set()
    allMoves = get_all_moves(board)
    for move in allMoves:
        if board.is_castling(move):
            if revise_move(board, move) == move:
                board.push(move)
                if len(board.attackers(board.turn, board.king(not board.turn)))>0:
                    intoCheckMoves.add(move)
                board.pop()
    return intoCheckMoves.union({move for move in list(board.pseudo_legal_moves) + [chess.Move.null()] if board.is_into_check(move)})

#Gets all moves that are actually legal (plus null) on at least one chessboard in fens
def get_pseudo_legal_moves(fens):
    legalMoves = set()
    legalMoves.add(chess.Move.null())
    for fen in fens:
        board = chess.Board(fen)
        legalMoves = legalMoves.union(board.pseudo_legal_moves)
        for move in get_all_moves(board):
            if board.is_castling(move) and revise_move(board, move) == move:
                    legalMoves.add(move)
    return legalMoves

GOOD_SENSING_SQUARES = [i*8 + j for i in range(1,7) for j in range(1,7)]

def without_pieces(board: chess.Board, color) -> chess.Board:
    """Returns a copy of `board` with the opponent's pieces removed."""
    mine = board.occupied_co[not color]
    return board.transform(lambda bb: bb & mine)

#If the turn was flipped, could someone play a checkmate move?
def opp_threatens_mate(board : chess.Board):
    board.push(chess.Move.null())
    threatens = does_threaten_mate(board)
    board.pop()
    return threatens

def does_threaten_mate(board : chess.Board):
    for move in get_pseudo_legal_moves({board.fen()}):
        revisedMove = revise_move(board, move) if move != chess.Move.null() else chess.Move.null()
        revisedMove = revisedMove or chess.Move.null()
        board.push(revisedMove)
        if board.is_checkmate(): ##TODO: Fix this so it only refers to the player whose turn it is in checkmate
            board.pop() #pop revised move
            return True
        board.pop()
    return False

# def get_threaten_mate_moves(board):
#     # return set()
#     threatenMateMoves = set()
#     for move in get_all_moves(board):
#         revisedMove = revise_move(board, move) if move != chess.Move.null() else chess.Move.null()
#         revisedMove = revisedMove or chess.Move.null()
#         board.push(revisedMove)
#         if would_threaten_mate(board):
#             threatenMateMoves.add(revisedMove)
#         board.pop()
#     return threatenMateMoves

def get_threaten_mate_moves_dist(dist):
    # return set()
    t = time.time()
    mostLikelyBoards = list(sorted(dist, key=dist.get, reverse=True))[:2]
    legalMoves = get_pseudo_legal_moves(dist)
    threatenMateMoves = set()
    for fen in mostLikelyBoards:
        if dist[fen] < .1:
            continue
        board = chess.Board(fen)
        allMoves = get_all_moves(board)
        for move in set(legalMoves).intersection(set(allMoves)):
            revisedMove = revise_move(board, move) if move != chess.Move.null() else chess.Move.null()
            revisedMove = revisedMove or chess.Move.null()
            if real_capture_square_of_move(board, revisedMove) != None:
                continue
            board.push(revisedMove)
            #if we (now opp because it's their turn) threaten checkmate...
            if opp_threatens_mate(board):
                threatenMateMoves.add(revisedMove)
            board.pop()
    # print(f"Got threatenMateMoves for dist of size {len(dist)} in {time.time() - t} seconds")
    return threatenMateMoves


def get_check_moves(board):
    checkMoves = set()
    for move in get_all_moves(board):
        revisedMove = revise_move(board, move) if move != chess.Move.null() else chess.Move.null()
        revisedMove = revisedMove or chess.Move.null()
        board.push(revisedMove)
        #Skip moves that would leave king in check
        if board.attackers(board.turn, board.king(not board.turn)):
            board.pop()
            continue
        #Check moves that move right next to the king don't count
        if board.king(board.turn) in board.attackers(board.turn, revisedMove.to_square):
            board.pop()
            continue
        if board.is_check():
            checkMoves.add(revisedMove)
        board.pop()
    return checkMoves

def get_silent_check_and_queenCheck_moves(board : chess.Board):
    checkMoves = set()
    queenCheckMoves = set()
    for move in get_all_moves(board):
        revisedMove = revise_move(board, move) if move != chess.Move.null() else chess.Move.null()
        revisedMove = revisedMove or chess.Move.null()
        if real_capture_square_of_move(board, revisedMove) != None:
            continue
        queenLocs = board.pieces(chess.QUEEN, not board.turn)
        board.push(revisedMove)
        #Skip moves that would leave king in check
        if board.attackers(board.turn, board.king(not board.turn)):
            board.pop()
            continue
        #Check moves that move right next to the king don't count
        if board.king(board.turn) in board.attackers(board.turn, revisedMove.to_square):
            board.pop()
            continue
        if board.is_check():
            checkMoves.add(revisedMove)
        if any([revisedMove.to_square in board.attackers(board.turn, queenLoc) for queenLoc in queenLocs]):
            queenCheckMoves.add(revisedMove)
        # if any([len(board.attackers(board.turn, queenLoc))>0 for queenLoc in queenLocs]):
        #     queenCheckMoves.add(revisedMove)
        board.pop()
    return checkMoves, set()#queenCheckMoves

def get_check_and_queenCheck_moves_dist(dist):
    legalMoves = get_pseudo_legal_moves(dist)
    checkMoves = set()
    queenCheckMoves = set()
    for fen in dist:
        board = chess.Board(fen)
        allMoves = get_all_moves(board)
        for move in set(legalMoves).intersection(set(allMoves)):
            revisedMove = revise_move(board, move) if move != chess.Move.null() else chess.Move.null()
            revisedMove = revisedMove or chess.Move.null()
            if real_capture_square_of_move(board, revisedMove) != None:
                continue
            queenLocs = board.pieces(chess.QUEEN, not board.turn)
            board.push(revisedMove)
            #Skip moves that would leave king in check
            if board.attackers(board.turn, board.king(not board.turn)):
                board.pop()
                continue
            #Check moves that move right next to the king don't count
            if board.king(board.turn) in board.attackers(board.turn, revisedMove.to_square):
                board.pop()
                continue
            if board.is_check():
                checkMoves.add(revisedMove)
            if any([revisedMove.to_square in board.attackers(board.turn, queenLoc) for queenLoc in queenLocs]):
                queenCheckMoves.add(revisedMove)
            board.pop()
    return checkMoves, set()#queenCheckMoves

def get_silent_check_and_queenCheck_moves_dist(dist):
    # print("Getting silent check moves...")
    startTime = time.time()
    legalMoves = get_pseudo_legal_moves(dist)
    checkMoves = set()
    queenCheckMoves = set()
    for fen in dist:
        board = chess.Board(fen)
        allMoves = get_all_moves(board)
        for move in set(legalMoves).intersection(set(allMoves)):
            revisedMove = revise_move(board, move) if move != chess.Move.null() else chess.Move.null()
            revisedMove = revisedMove or chess.Move.null()
            if real_capture_square_of_move(board, revisedMove) != None:
                continue
            queenLocs = board.pieces(chess.QUEEN, not board.turn)
            board.push(revisedMove)
            #Skip moves that would leave king in check
            if board.attackers(board.turn, board.king(not board.turn)):
                board.pop()
                continue
            #Check moves that move right next to the king don't count
            if board.king(board.turn) in board.attackers(board.turn, revisedMove.to_square):
                board.pop()
                continue
            if board.is_check():
                checkMoves.add(revisedMove)
            if any([revisedMove.to_square in board.attackers(board.turn, queenLoc) for queenLoc in queenLocs]):
                queenCheckMoves.add(revisedMove)
            board.pop()
    # print(f"Got silent check moves in {time.time()-startTime} seconds")
    return checkMoves, set()#queenCheckMoves

def percent_check(boardDist):
    percent = 0
    kingSquares = defaultdict(float)
    for fen, prob in boardDist.items():
        board = chess.Board(fen)
        if len(board.attackers(board.turn, board.king(not board.turn))) > 0:
            percent += prob
        kingSquares[board.king(not board.turn)] += prob
    return percent, kingSquares

def percent_in_check(boardDist):
    percent = 0
    checkerSquares = defaultdict(float)
    for fen, prob in boardDist.items():
        board = chess.Board(fen)
        checkers = board.checkers()
        if len(checkers) > 0:
            percent += prob
        for checker in checkers:
            checkerSquares[checker] += prob
    return percent, checkerSquares

def real_capture_square_of_move(board: chess.Board, move: Optional[chess.Move]) -> Optional[Square]:
    capture_square = None
    if move is not None and board.is_capture(move) and move in get_pseudo_legal_moves({board.fen()}):
        if board.is_en_passant(move):
            # taken from :func:`chess.Board.push()`
            down = -8 if board.turn == chess.WHITE else 8
            capture_square = board.ep_square + down
        else:
            capture_square = move.to_square
    return capture_square